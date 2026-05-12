"""Layer 5 — max_hold learner.

Spec source: vault/30_components/layer-5-learner-network.md (Q1, Q6).

Tunes the **max holding bars** ceiling per strategy. Stats are accumulated by
holding bucket so the committed ``value`` can pick the bucket with the best
realised expectancy.

Key format: ``"<strategy_id>"`` (one row per strategy).

Sufficient stats stored across multiple buckets is non-trivial in the flat
``learner_state`` row, so we reuse the existing columns and roll a coarse
3-bucket accumulator into a JSON-encoded ``pending_delta``-style channel via a
secondary ``key`` namespace::

    "<strategy_id>"          → committed value (max hold bars, e.g. 60.0)
    "<strategy_id>:bucket:0" → short-hold avg pnl  (≤ 0.5 × expected)
    "<strategy_id>:bucket:1" → mid-hold avg pnl    (~ 1.0 × expected)
    "<strategy_id>:bucket:2" → long-hold avg pnl   (> 1.5 × expected)

Commit logic per strategy row:
  - read 3 bucket rows (skip if all n_eff < 20).
  - choose the bucket with the highest expectancy (avg_pnl_r).
  - target value = ``expected_holding_bars × bucket_factor`` where factor is
    0.5 / 1.0 / 1.5 — but the +/-0.1/hour clip means the actual change per hour
    is small (we are tuning the ceiling, not jumping to it). Hourly cap is
    applied generically by ``BaseLearner.commit_hourly``, so even a large
    target only nudges the committed value by at most ``LEARNER_DELTA_HOURLY_CAP``.

Sparse fallback (vault Q6): ``StrategyMetadata.expected_holding_bars`` (caller
supplies via constructor; defaults to ``DEFAULT_MAX_HOLD_FALLBACK_BARS``).

NOTE: this learner stores **bars**, not a multiplier. Consumers (``running-paper-loop``)
read ``get_mult().value`` and treat it as bars directly. The individual clip
[0.3, 3.0] does NOT apply — we override ``get_mult`` accordingly.
"""

from __future__ import annotations

from polaris.core.learners.base import (
    DEFAULT_MAX_HOLD_FALLBACK_BARS,
    LEARNER_MIN_NEFF_FOR_DELTA,
    NEUTRAL_MULT,
    BaseLearner,
    ClosedTrade,
    HourlyCommitReport,
    LearnerMultResult,
)

# Bucket boundaries as multiples of the strategy's expected_holding_bars.
BUCKET_FACTORS: tuple[float, ...] = (0.5, 1.0, 1.5)


class MaxHoldLearner(BaseLearner):
    learner_id = "max_hold"

    def __init__(self, conn, *, expected_holding_bars: int = DEFAULT_MAX_HOLD_FALLBACK_BARS) -> None:  # type: ignore[no-untyped-def]
        super().__init__(conn)
        self.expected_holding_bars = max(1, int(expected_holding_bars))

    # ``max_hold`` stores **bars**, not a multiplier — bypass the [0.3, 3.0]
    # multiplier clip that ``BaseLearner._upsert_state`` would otherwise apply.
    # Without this override the strategy-level row would be capped to 3.0 bars
    # and every fresh trade would write that capped value, collapsing the
    # winner-extension baseline (codex Day 4 R1 P0).
    def _upsert_state(self, key: str, stats: dict[str, float], *, ts: int) -> None:
        # Use the raw value (bars), only enforcing a sane lower bound of 1.
        value = max(1.0, float(stats.get("value", float(self.expected_holding_bars))))
        self.conn.execute(
            "INSERT INTO learner_state "
            "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
            " pending_delta, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(learner_id, key) DO UPDATE SET "
            " value = excluded.value, "
            " n_eff = excluded.n_eff, "
            " wins_eff = excluded.wins_eff, "
            " pnl_r_sum_eff = excluded.pnl_r_sum_eff, "
            " pending_delta = excluded.pending_delta, "
            " updated_at = excluded.updated_at",
            (
                self.learner_id,
                key,
                value,
                stats.get("n_eff", 0.0),
                stats.get("wins_eff", 0.0),
                stats.get("pnl_r_sum_eff", 0.0),
                stats.get("pending_delta", 0.0),
                ts,
            ),
        )

    def key_for(self, trade: ClosedTrade) -> str:
        return trade.strategy_id

    def _key_for_lookup(
        self,
        *,
        ticker: str,
        strategy_id: str,
        regime: str,
        session: str,
    ) -> str:
        return strategy_id

    def fallback_value(self, key: str) -> float:
        return float(self.expected_holding_bars)

    def _bucket_index(self, holding_bars: int) -> int:
        ratio = holding_bars / max(1.0, float(self.expected_holding_bars))
        if ratio <= BUCKET_FACTORS[0]:
            return 0
        if ratio <= BUCKET_FACTORS[1]:
            return 1
        return 2

    def observe(
        self,
        prior: dict[str, float],
        trade: ClosedTrade,
    ) -> dict[str, float]:
        # Strategy-level row: increment overall stats.
        # Seed the strategy row at expected_holding_bars (codex Day 4 R1 P0):
        # the learner tunes around that baseline; ``BaseLearner`` defaults to
        # NEUTRAL_MULT=1.0 which would collapse winner-hold to ~1 bar.
        n_eff = prior.get("n_eff", 0.0) + 1.0
        wins_eff = prior.get("wins_eff", 0.0) + (1.0 if trade.won else 0.0)
        pnl_sum = prior.get("pnl_r_sum_eff", 0.0) + trade.pnl_r
        seeded_value = prior.get("value", NEUTRAL_MULT)
        if seeded_value <= 1.0 + 1e-9:
            seeded_value = float(self.expected_holding_bars)
        # Side effect: also write the per-bucket row.
        bucket_key = f"{trade.strategy_id}:bucket:{self._bucket_index(trade.holding_bars)}"
        bucket_row = self.conn.execute(
            "SELECT n_eff, wins_eff, pnl_r_sum_eff FROM learner_state "
            "WHERE learner_id = ? AND key = ?",
            (self.learner_id, bucket_key),
        ).fetchone()
        if bucket_row is None:
            b_n, b_w, b_p = 0.0, 0.0, 0.0
        else:
            b_n, b_w, b_p = float(bucket_row[0]), float(bucket_row[1]), float(bucket_row[2])
        b_n += 1.0
        b_w += 1.0 if trade.won else 0.0
        b_p += trade.pnl_r
        self.conn.execute(
            "INSERT INTO learner_state "
            "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
            " pending_delta, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0.0, ?) "
            "ON CONFLICT(learner_id, key) DO UPDATE SET "
            " n_eff = excluded.n_eff, wins_eff = excluded.wins_eff, "
            " pnl_r_sum_eff = excluded.pnl_r_sum_eff, "
            " updated_at = excluded.updated_at",
            (
                self.learner_id,
                bucket_key,
                NEUTRAL_MULT,
                b_n,
                b_w,
                b_p,
                trade.closed_ts,
            ),
        )
        return {
            "value": seeded_value,
            "n_eff": n_eff,
            "wins_eff": wins_eff,
            "pnl_r_sum_eff": pnl_sum,
            "pending_delta": prior.get("pending_delta", 0.0),
        }

    def compute_value_from_stats(self, stats: dict[str, float]) -> float:
        # Find best bucket among "<strategy_id>:bucket:0..2" siblings.
        # We don't know the strategy_id from stats alone, so we fall back to
        # leaving the value unchanged here; the bucket lookup happens in a
        # specialised path. We override commit_hourly to handle this.
        return stats.get("value", float(self.expected_holding_bars))

    def get_mult(
        self,
        *,
        ticker: str,
        strategy_id: str,
        regime: str,
        session: str = "asia",
    ) -> LearnerMultResult:
        if not self.enabled:
            return LearnerMultResult(
                value=self.fallback_value(strategy_id), source="disabled"
            )
        row = self.conn.execute(
            "SELECT value, n_eff FROM learner_state "
            "WHERE learner_id = ? AND key = ?",
            (self.learner_id, strategy_id),
        ).fetchone()
        if row is None:
            return LearnerMultResult(
                value=self.fallback_value(strategy_id), source="fallback_neutral"
            )
        value, n_eff = float(row[0]), float(row[1])
        if n_eff < LEARNER_MIN_NEFF_FOR_DELTA:
            return LearnerMultResult(
                value=self.fallback_value(strategy_id),
                source="sparse",
                n_eff=n_eff,
            )
        return LearnerMultResult(
            value=max(1.0, value), source="live", n_eff=n_eff
        )

    def commit_hourly(self, *, now_ts: int | None = None) -> HourlyCommitReport:
        # Per-strategy commit using bucket avg_pnl_r.
        # Atomicity (codex Day 4 R1 P1 fix): every read + write + snapshot
        # inside one BEGIN IMMEDIATE / COMMIT envelope.
        import time  # noqa: PLC0415

        from polaris.core.learners.base import (  # noqa: PLC0415
            clip_hourly_delta,
        )

        ts = int(now_ts if now_ts is not None else time.time())
        report = HourlyCommitReport(learner_id=self.learner_id, keys_updated=0)
        if not self.enabled:
            return report
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self.conn.execute(
                "SELECT key, value, n_eff FROM learner_state "
                "WHERE learner_id = ? AND key NOT LIKE '%:bucket:%'",
                (self.learner_id,),
            ).fetchall()
            for r in rows:
                strategy_id = r[0]
                cur_value = float(r[1])
                n_eff = float(r[2])
                if n_eff < LEARNER_MIN_NEFF_FOR_DELTA:
                    continue
                # find best bucket (read inside the same transaction)
                best_idx, best_avg = -1, -float("inf")
                for idx in range(3):
                    bk = f"{strategy_id}:bucket:{idx}"
                    bk_row = self.conn.execute(
                        "SELECT n_eff, pnl_r_sum_eff FROM learner_state "
                        "WHERE learner_id = ? AND key = ?",
                        (self.learner_id, bk),
                    ).fetchone()
                    if bk_row is None:
                        continue
                    bn, bp = float(bk_row[0]), float(bk_row[1])
                    if bn <= 0.0:
                        continue
                    avg = bp / bn
                    if avg > best_avg:
                        best_avg, best_idx = avg, idx
                if best_idx < 0:
                    continue
                target_bars = (
                    BUCKET_FACTORS[best_idx] * float(self.expected_holding_bars)
                )
                # nudge cur_value toward target_bars (bars-units) capped at ±0.1*expected.
                bar_cap = max(1.0, 0.1 * float(self.expected_holding_bars))
                raw_delta = target_bars - cur_value
                if raw_delta > bar_cap:
                    delta = bar_cap
                elif raw_delta < -bar_cap:
                    delta = -bar_cap
                else:
                    delta = raw_delta
                # also re-apply the generic LEARNER_DELTA_HOURLY_CAP scaled by
                # expected bars so the +/-0.1 mult cap is preserved in spirit.
                delta = clip_hourly_delta(delta / max(1.0, float(self.expected_holding_bars)))
                delta *= float(self.expected_holding_bars)
                new_value = max(1.0, cur_value + delta)
                self.conn.execute(
                    "UPDATE learner_state SET value = ?, pending_delta = 0.0, "
                    "updated_at = ? WHERE learner_id = ? AND key = ?",
                    (new_value, ts, self.learner_id, strategy_id),
                )
                report.keys_updated += 1
                report.deltas_applied[strategy_id] = delta
            report.snapshot_ts = self._write_snapshot(now_ts=ts)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self._flush_disk_snapshot()
        return report


__all__ = ["MaxHoldLearner", "BUCKET_FACTORS"]
