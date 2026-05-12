"""Layer 5 — BaseLearner ABC + clip helpers + adaptive_learner_attack constants.

Spec source: vault/30_components/layer-5-learner-network.md (Q1-Q6).
ADR-007: 4 principles — generous default / temporary block / specific triple / toggle.

Pure functions + protocol surface only. SQLite I/O lives in
``polaris.core.learners.{session,regime,max_hold}`` (per-learner) and
``polaris.core.learners.scheduler`` (orchestration).

Design notes:
- Incremental stats per close → ``record_trade_close()``.
- Hourly commit → ``commit_hourly()`` writes ``pending_delta`` into ``value`` and
  resets pending. Caller cron is the scheduler.
- adaptive_learner_attack triple block: SQLite-resident, 1h auto-unblock,
  ``size_mult=0.3`` (entry allowed — 4 principles: generous default).
- Toggle fallback: ``enabled=False`` → caller must use hardcoded default.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (P0)  — see vault/30_components/layer-5-learner-network.md
# ---------------------------------------------------------------------------

LEARNER_MIN_NEFF_FOR_DELTA: Final[float] = 20.0
"""Minimum n_eff before a learner is allowed to commit a delta."""

LEARNER_DELTA_HOURLY_CAP: Final[float] = 0.1
"""Per-key per-hour absolute delta ceiling (sanity bound)."""

LEARNER_INDIVIDUAL_MULT_CLIP: Final[tuple[float, float]] = (0.3, 3.0)
"""ADR-007 individual multiplier clip — saturation guard."""

LEARNER_PRODUCT_CLIP: Final[tuple[float, float]] = (0.1, 5.0)
"""ADR-007 final composed multiplier clip."""

WR_PROMOTE_THRESHOLD: Final[float] = 0.55
"""regime_mult promotion threshold (≥55% → +0.1)."""

WR_DEMOTE_THRESHOLD: Final[float] = 0.40
"""regime_mult demotion threshold (≤40% → -0.1)."""

TRIPLE_BLOCK_NEFF_THRESHOLD: Final[float] = 20.0
"""Minimum trades before triple block evaluates."""

TRIPLE_BLOCK_WR_THRESHOLD: Final[float] = 0.30
"""WR ceiling that triggers a triple block."""

TRIPLE_BLOCK_EXPECTANCY_THRESHOLD: Final[float] = -0.25
"""Expectancy R floor that triggers a triple block."""

TRIPLE_BLOCK_DURATION_SEC: Final[int] = 3600
"""Auto-unblock interval (1h) — ADR-007 principle 2."""

TRIPLE_BLOCK_SIZE_MULT: Final[float] = 0.3
"""Block applied as a size multiplier (entry remains allowed)."""

NEUTRAL_MULT: Final[float] = 1.0
"""Default fallback multiplier when sparse / disabled."""

DEFAULT_MAX_HOLD_FALLBACK_BARS: Final[int] = 60
"""max_hold fallback when no StrategyMetadata.expected_holding_bars."""

LEARNER_SNAPSHOT_DIR: Final[Path] = Path("data/learner_snapshots")
"""Filesystem location of SQLite hot-backup + JSON manifest snapshots
(spec: vault/30_components/layer-5-learner-network.md §Q3)."""

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """Trade close event consumed by every learner update."""

    trade_id: str
    strategy_id: str
    ticker: str
    venue: str
    regime: str
    session: str
    pnl_r: float
    won: bool
    holding_bars: int
    closed_ts: int


@dataclass(frozen=True, slots=True)
class LearnerMultResult:
    """``get_mult`` output, surfacing fallback/source for audit."""

    value: float
    source: str  # "live" / "fallback_neutral" / "disabled" / "sparse"
    n_eff: float = 0.0


@dataclass(frozen=True, slots=True)
class TripleBlockEntry:
    """Active triple block row."""

    ticker: str
    strategy_id: str
    regime: str
    size_mult: float
    reason: str
    source_learner: str
    blocked_until_ts: int
    created_ts: int


@dataclass(slots=True)
class HourlyCommitReport:
    """Outcome from one ``commit_hourly`` invocation (audit + smoke)."""

    learner_id: str
    keys_updated: int
    deltas_applied: dict[str, float] = field(default_factory=dict)
    snapshot_ts: int = 0


# ---------------------------------------------------------------------------
# Clip helpers
# ---------------------------------------------------------------------------


def clip_individual_mult(value: float) -> float:
    """Clip a single learner multiplier to ``LEARNER_INDIVIDUAL_MULT_CLIP``."""
    if not math.isfinite(value):
        return NEUTRAL_MULT
    lo, hi = LEARNER_INDIVIDUAL_MULT_CLIP
    return max(lo, min(hi, value))


def clip_product_mult(value: float) -> float:
    """Clip the composed product multiplier to ``LEARNER_PRODUCT_CLIP``."""
    if not math.isfinite(value):
        return NEUTRAL_MULT
    lo, hi = LEARNER_PRODUCT_CLIP
    return max(lo, min(hi, value))


def clip_hourly_delta(delta: float) -> float:
    """Clip a single-hour delta proposal to ``±LEARNER_DELTA_HOURLY_CAP``."""
    if not math.isfinite(delta):
        return 0.0
    return max(-LEARNER_DELTA_HOURLY_CAP, min(LEARNER_DELTA_HOURLY_CAP, delta))


def resolve_final_size_mult(
    *,
    session_mult: float,
    regime_mult: float,
    cell_routing_mult: float,
    ai_feedback_weight: float = NEUTRAL_MULT,
) -> float:
    """Independent-axis composition (vault Q2 — multiplicative chain).

    Each input is individually clipped before composition; the product is then
    re-clipped to keep the cumulative multiplier sane.
    """
    s = clip_individual_mult(session_mult)
    r = clip_individual_mult(regime_mult)
    c = clip_individual_mult(cell_routing_mult)
    a = clip_individual_mult(ai_feedback_weight)
    return clip_product_mult(s * r * c * a)


# ---------------------------------------------------------------------------
# BaseLearner ABC
# ---------------------------------------------------------------------------


class BaseLearner(ABC):
    """Abstract base for incremental-stats + hourly-commit learners.

    Subclasses **must** implement:
      - ``learner_id`` (class attribute / property).
      - ``key_for(trade)`` → string key for ``learner_state``.
      - ``observe(trade)`` → update sufficient stats (called by ``update``).
      - ``compute_value_from_stats(stats)`` → derive ``value`` for ``get_mult``.

    The base class supplies the SQLite plumbing: row read/write, per-hour
    delta clip, snapshot + fallback handling, and the
    ``adaptive_learner_attack`` toggle.
    """

    learner_id: str = "base"
    enabled: bool = True

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._pending_disk_snapshot: tuple[int, Mapping[str, Any]] | None = None

    # -- subclass hooks ---------------------------------------------------

    @abstractmethod
    def key_for(self, trade: ClosedTrade) -> str:
        """Return composite key for this trade in ``learner_state.key``."""

    @abstractmethod
    def observe(
        self,
        prior: dict[str, float],
        trade: ClosedTrade,
    ) -> dict[str, float]:
        """Fold ``trade`` into the running stats dict.

        ``prior`` keys: ``n_eff``, ``wins_eff``, ``pnl_r_sum_eff``,
        ``pending_delta``, ``value``. Returns a new dict.
        """

    @abstractmethod
    def compute_value_from_stats(self, stats: dict[str, float]) -> float:
        """Derive the *committed* ``value`` (multiplier) from sufficient stats.

        Called during ``commit_hourly``. Subclasses encode the WR / bucket /
        threshold logic here.
        """

    def fallback_value(self, key: str) -> float:
        """Sparse / disabled fallback. Default = 1.0 (neutral)."""
        return NEUTRAL_MULT

    # -- core operations --------------------------------------------------

    def update(self, trade: ClosedTrade, *, now_ts: int | None = None) -> None:
        """Incremental observe (called per trade close)."""
        if not self.enabled:
            logger.debug(
                "[learner %s] update skipped — disabled (trade_id=%s)",
                self.learner_id,
                trade.trade_id,
            )
            return
        ts = int(now_ts if now_ts is not None else time.time())
        key = self.key_for(trade)
        prior = self._fetch_state(key)
        new_stats = self.observe(prior, trade)
        self._upsert_state(key, new_stats, ts=ts)
        logger.info(
            "[learner %s] update key=%s trade=%s pnl_r=%.3f won=%s "
            "n_eff=%.1f→%.1f wins_eff=%.1f→%.1f",
            self.learner_id,
            key,
            trade.trade_id,
            trade.pnl_r,
            trade.won,
            prior.get("n_eff", 0.0),
            new_stats.get("n_eff", 0.0),
            prior.get("wins_eff", 0.0),
            new_stats.get("wins_eff", 0.0),
        )
        # adaptive_learner_attack triple block evaluation (specific axis only)
        self._maybe_emit_triple_block(trade, ts)

    def get_mult(
        self,
        *,
        ticker: str,
        strategy_id: str,
        regime: str,
        session: str = "asia",
    ) -> LearnerMultResult:
        """Read-only multiplier lookup (used by sizing pipeline)."""
        if not self.enabled:
            return LearnerMultResult(
                value=self.fallback_value(""),
                source="disabled",
            )
        key = self._key_for_lookup(
            ticker=ticker, strategy_id=strategy_id, regime=regime, session=session
        )
        row = self.conn.execute(
            "SELECT value, n_eff FROM learner_state WHERE learner_id = ? AND key = ?",
            (self.learner_id, key),
        ).fetchone()
        if row is None:
            return LearnerMultResult(
                value=self.fallback_value(key), source="fallback_neutral"
            )
        value, n_eff = float(row[0]), float(row[1])
        if n_eff < LEARNER_MIN_NEFF_FOR_DELTA:
            return LearnerMultResult(
                value=self.fallback_value(key), source="sparse", n_eff=n_eff
            )
        return LearnerMultResult(value=clip_individual_mult(value), source="live", n_eff=n_eff)

    def commit_hourly(self, *, now_ts: int | None = None) -> HourlyCommitReport:
        """Atomic hourly delta commit — recomputes ``value`` from stats.

        Process per row:
          1. Skip rows where ``n_eff < LEARNER_MIN_NEFF_FOR_DELTA``.
          2. Recompute the candidate value from current stats.
          3. ``delta = candidate - current_value`` (clipped to ±LEARNER_DELTA_HOURLY_CAP).
          4. Write committed value, reset ``pending_delta``.

        Atomicity (codex Day 4 R1 P1 fix): every read AND write happens inside
        the same ``BEGIN IMMEDIATE`` / ``COMMIT`` envelope, including the
        post-commit snapshot. ``BEGIN IMMEDIATE`` takes the writer lock up
        front, so concurrent ``record_trade_close`` writers wait — no
        read-modify-write race window between the candidate read and the
        update, and the snapshot reflects exactly the state this transaction
        committed.
        """
        ts = int(now_ts if now_ts is not None else time.time())
        report = HourlyCommitReport(learner_id=self.learner_id, keys_updated=0)
        if not self.enabled:
            return report
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self.conn.execute(
                "SELECT key, value, n_eff, wins_eff, pnl_r_sum_eff, pending_delta "
                "FROM learner_state WHERE learner_id = ?",
                (self.learner_id,),
            ).fetchall()
            for r in rows:
                key = r[0]
                stats = {
                    "value": float(r[1]),
                    "n_eff": float(r[2]),
                    "wins_eff": float(r[3]),
                    "pnl_r_sum_eff": float(r[4]),
                    "pending_delta": float(r[5]),
                }
                if stats["n_eff"] < LEARNER_MIN_NEFF_FOR_DELTA:
                    continue
                candidate = self.compute_value_from_stats(stats)
                if not math.isfinite(candidate):
                    continue
                raw_delta = candidate - stats["value"]
                delta = clip_hourly_delta(raw_delta)
                new_value = clip_individual_mult(stats["value"] + delta)
                self.conn.execute(
                    "UPDATE learner_state SET value = ?, pending_delta = 0.0, "
                    "updated_at = ? WHERE learner_id = ? AND key = ?",
                    (new_value, ts, self.learner_id, key),
                )
                report.keys_updated += 1
                report.deltas_applied[key] = delta
            # snapshot inside the same transaction so it reflects commit state
            report.snapshot_ts = self._write_snapshot(now_ts=ts)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        # Disk snapshot (SQLite hot backup + JSON manifest) runs outside the
        # transaction so it does not deadlock against the writer lock. The
        # in-DB row snapshot above is the rollback SSOT; the disk pair is
        # operator-facing and best-effort.
        self._flush_disk_snapshot()
        logger.info(
            "[learner %s] hourly commit — keys_updated=%d snapshot_ts=%s",
            self.learner_id,
            report.keys_updated,
            report.snapshot_ts,
        )
        return report

    def rollback(self, snapshot_ts: int) -> int:
        """Restore this learner from a JSON snapshot row.

        Returns the number of keys restored. Raises if the snapshot is missing.
        """
        logger.warning(
            "[learner %s] rollback to snapshot_ts=%s",
            self.learner_id,
            snapshot_ts,
        )
        row = self.conn.execute(
            "SELECT payload_json FROM learner_snapshot "
            "WHERE snapshot_ts = ? AND learner_id = ?",
            (snapshot_ts, self.learner_id),
        ).fetchone()
        if row is None:
            raise LookupError(
                f"snapshot {snapshot_ts} for learner {self.learner_id} not found"
            )
        payload = json.loads(row[0])
        keys = payload.get("rows", [])
        ts = int(time.time())
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "DELETE FROM learner_state WHERE learner_id = ?", (self.learner_id,)
            )
            for k in keys:
                self.conn.execute(
                    "INSERT INTO learner_state "
                    "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
                    " pending_delta, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.learner_id,
                        k["key"],
                        float(k["value"]),
                        float(k["n_eff"]),
                        float(k["wins_eff"]),
                        float(k["pnl_r_sum_eff"]),
                        0.0,
                        ts,
                    ),
                )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return len(keys)

    # -- helpers ----------------------------------------------------------

    def _fetch_state(self, key: str) -> dict[str, float]:
        row = self.conn.execute(
            "SELECT value, n_eff, wins_eff, pnl_r_sum_eff, pending_delta "
            "FROM learner_state WHERE learner_id = ? AND key = ?",
            (self.learner_id, key),
        ).fetchone()
        if row is None:
            return {
                "value": NEUTRAL_MULT,
                "n_eff": 0.0,
                "wins_eff": 0.0,
                "pnl_r_sum_eff": 0.0,
                "pending_delta": 0.0,
            }
        return {
            "value": float(row[0]),
            "n_eff": float(row[1]),
            "wins_eff": float(row[2]),
            "pnl_r_sum_eff": float(row[3]),
            "pending_delta": float(row[4]),
        }

    def _upsert_state(
        self,
        key: str,
        stats: dict[str, float],
        *,
        ts: int,
    ) -> None:
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
                clip_individual_mult(stats.get("value", NEUTRAL_MULT)),
                stats.get("n_eff", 0.0),
                stats.get("wins_eff", 0.0),
                stats.get("pnl_r_sum_eff", 0.0),
                stats.get("pending_delta", 0.0),
                ts,
            ),
        )

    def _key_for_lookup(
        self,
        *,
        ticker: str,
        strategy_id: str,
        regime: str,
        session: str,
    ) -> str:
        """Default lookup key composer; subclasses override as needed."""
        return f"{strategy_id}:{regime}"

    def _write_snapshot(self, *, now_ts: int) -> int:
        """Write the in-DB row snapshot (atomic, called inside transaction).

        The on-disk SQLite hot-backup + JSON manifest pair (spec §Q3) is
        written by ``_write_disk_snapshot`` AFTER the transaction commits —
        ``sqlite3.Connection.backup`` would deadlock against the writer
        lock held by ``BEGIN IMMEDIATE`` if we tried to backup here.
        """
        rows = self.conn.execute(
            "SELECT key, value, n_eff, wins_eff, pnl_r_sum_eff, pending_delta "
            "FROM learner_state WHERE learner_id = ?",
            (self.learner_id,),
        ).fetchall()
        payload = {
            "rows": [
                {
                    "key": r[0],
                    "value": float(r[1]),
                    "n_eff": float(r[2]),
                    "wins_eff": float(r[3]),
                    "pnl_r_sum_eff": float(r[4]),
                    "pending_delta": float(r[5]),
                }
                for r in rows
            ],
        }
        self.conn.execute(
            "INSERT INTO learner_snapshot (snapshot_ts, learner_id, payload_json) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(snapshot_ts, learner_id) DO UPDATE SET "
            " payload_json = excluded.payload_json",
            (now_ts, self.learner_id, json.dumps(payload, separators=(",", ":"))),
        )
        # Stash the payload so the post-commit disk emitter has the exact
        # rows that just landed, without a re-query.
        self._pending_disk_snapshot = (now_ts, payload)
        return now_ts

    def _flush_disk_snapshot(self) -> None:
        """Emit the queued on-disk snapshot, then clear the pending slot."""
        if self._pending_disk_snapshot is None:
            return
        now_ts, payload = self._pending_disk_snapshot
        self._pending_disk_snapshot = None
        self._write_disk_snapshot(now_ts=now_ts, payload=payload)

    def _write_disk_snapshot(
        self, *, now_ts: int, payload: Mapping[str, Any]
    ) -> None:
        """Best-effort SQLite hot backup + manifest JSON.

        Spec §Q3 (vault/30_components/layer-5-learner-network.md):
        ``data/learner_snapshots/<ts>.db`` (full DB hot backup) plus
        ``data/learner_snapshots/<ts>.json`` (manifest with this learner's
        rows). The DB file is shared across learners in the same hour
        (idempotent re-open), while each learner emits its own manifest.

        Skipped automatically when the connection is a ``:memory:`` DB
        (tests, smoke without on-disk path) — the in-DB ``learner_snapshot``
        row already captures the state and is the SSOT for rollback.
        Failures are non-fatal so an ephemeral CI sandbox cannot block
        a learner commit.
        """
        try:
            # ``PRAGMA database_list`` returns the path of the main schema;
            # an empty/`:memory:` value means we have no source file to back
            # up. Skip silently in that case.
            row = self.conn.execute(
                "PRAGMA database_list"
            ).fetchone()
            db_file = "" if row is None else (row[2] or "")
            if not db_file or db_file == ":memory:":
                return
            base = LEARNER_SNAPSHOT_DIR
            base.mkdir(parents=True, exist_ok=True)
            db_path = base / f"{now_ts}.db"
            manifest_path = base / f"{now_ts}.{self.learner_id}.json"
            if not db_path.exists():
                with sqlite3.connect(str(db_path)) as dest:
                    self.conn.backup(dest)
            manifest_payload = {
                "snapshot_ts": now_ts,
                "learner_id": self.learner_id,
                "rows": payload.get("rows", []),
            }
            manifest_path.write_text(
                json.dumps(manifest_payload, separators=(",", ":"))
            )
        except (sqlite3.Error, OSError):
            return

    def _maybe_emit_triple_block(self, trade: ClosedTrade, ts: int) -> None:
        """ADR-007 §triple specific block.

        Triggered from regime_mult only (single source-of-truth for blocks).
        Subclasses that should never emit a block leave ``learner_id !=
        regime_mult`` and this is a no-op.
        """
        if self.learner_id != "regime_mult":
            return
        # n_eff / wr / expectancy from the just-updated row
        triple_key = f"{trade.ticker}|{trade.strategy_id}|{trade.regime}"
        row = self.conn.execute(
            "SELECT n_eff, wins_eff, pnl_r_sum_eff FROM learner_state "
            "WHERE learner_id = 'triple_stats' AND key = ?",
            (triple_key,),
        ).fetchone()
        if row is None:
            n_eff, wins, pnl_sum = 0.0, 0.0, 0.0
        else:
            n_eff, wins, pnl_sum = float(row[0]), float(row[1]), float(row[2])
        n_eff += 1.0
        wins += 1.0 if trade.won else 0.0
        pnl_sum += trade.pnl_r
        self.conn.execute(
            "INSERT INTO learner_state "
            "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
            " pending_delta, updated_at) "
            "VALUES ('triple_stats', ?, 1.0, ?, ?, ?, 0.0, ?) "
            "ON CONFLICT(learner_id, key) DO UPDATE SET "
            " n_eff = excluded.n_eff, wins_eff = excluded.wins_eff, "
            " pnl_r_sum_eff = excluded.pnl_r_sum_eff, "
            " updated_at = excluded.updated_at",
            (triple_key, n_eff, wins, pnl_sum, ts),
        )
        if n_eff < TRIPLE_BLOCK_NEFF_THRESHOLD:
            return
        wr = wins / n_eff if n_eff > 0 else 0.0
        expectancy = pnl_sum / n_eff if n_eff > 0 else 0.0
        if wr <= TRIPLE_BLOCK_WR_THRESHOLD and expectancy <= TRIPLE_BLOCK_EXPECTANCY_THRESHOLD:
            until = ts + TRIPLE_BLOCK_DURATION_SEC
            logger.warning(
                "[learner %s] triple BLOCK %s|%s|%s wr=%.2f exp=%.2f size_mult=%.2f until=%d",
                self.learner_id,
                trade.ticker,
                trade.strategy_id,
                trade.regime,
                wr,
                expectancy,
                TRIPLE_BLOCK_SIZE_MULT,
                until,
            )
            self.conn.execute(
                "INSERT INTO learner_blocks "
                "(ticker, strategy_id, regime, size_mult, reason, source_learner, "
                " blocked_until_ts, created_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(ticker, strategy_id, regime) DO UPDATE SET "
                " size_mult = excluded.size_mult, "
                " reason = excluded.reason, "
                " source_learner = excluded.source_learner, "
                " blocked_until_ts = excluded.blocked_until_ts, "
                " created_ts = excluded.created_ts",
                (
                    trade.ticker,
                    trade.strategy_id,
                    trade.regime,
                    TRIPLE_BLOCK_SIZE_MULT,
                    f"wr={wr:.2f},exp={expectancy:.2f}",
                    self.learner_id,
                    until,
                    ts,
                ),
            )


# ---------------------------------------------------------------------------
# Triple block lookup (read-only, used by sizing/pre-entry pipeline)
# ---------------------------------------------------------------------------


def evaluate_triple_block(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    strategy_id: str,
    regime: str,
    now_ts: int,
) -> TripleBlockEntry | None:
    """Return active block for ``(ticker, strategy_id, regime)`` or ``None``.

    Auto-unblock: a row whose ``blocked_until_ts <= now_ts`` is treated as
    expired (and lazily not returned). Eviction is handled by the scheduler
    so concurrent readers don't race each other.
    """
    row = conn.execute(
        "SELECT ticker, strategy_id, regime, size_mult, reason, source_learner, "
        " blocked_until_ts, created_ts FROM learner_blocks "
        "WHERE ticker = ? AND strategy_id = ? AND regime = ?",
        (ticker, strategy_id, regime),
    ).fetchone()
    if row is None:
        return None
    if int(row[6]) <= int(now_ts):
        return None
    return TripleBlockEntry(
        ticker=row[0],
        strategy_id=row[1],
        regime=row[2],
        size_mult=float(row[3]),
        reason=row[4],
        source_learner=row[5],
        blocked_until_ts=int(row[6]),
        created_ts=int(row[7]),
    )


def evict_expired_triple_blocks(conn: sqlite3.Connection, *, now_ts: int) -> int:
    """Delete rows whose ``blocked_until_ts <= now_ts``. Returns count."""
    cur = conn.execute(
        "DELETE FROM learner_blocks WHERE blocked_until_ts <= ?", (int(now_ts),)
    )
    return int(cur.rowcount or 0)


def restore_snapshot_from_disk(
    conn: sqlite3.Connection,
    *,
    snapshot_ts: int,
    learner_id: str,
    snapshot_dir: Path = LEARNER_SNAPSHOT_DIR,
) -> int:
    """Manual-apply rollback path (spec §Q3 — operator-driven).

    Reads ``<snapshot_dir>/<snapshot_ts>.<learner_id>.json`` and replays the
    rows into ``learner_state`` for the given learner. Returns row count.
    Raises :class:`FileNotFoundError` if the manifest is missing.
    """
    manifest_path = snapshot_dir / f"{snapshot_ts}.{learner_id}.json"
    if not manifest_path.exists():
        raise FileNotFoundError(str(manifest_path))
    payload = json.loads(manifest_path.read_text())
    rows = payload.get("rows", [])
    ts = int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "DELETE FROM learner_state WHERE learner_id = ?", (learner_id,)
        )
        for k in rows:
            conn.execute(
                "INSERT INTO learner_state "
                "(learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
                " pending_delta, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0.0, ?)",
                (
                    learner_id,
                    k["key"],
                    float(k["value"]),
                    float(k["n_eff"]),
                    float(k["wins_eff"]),
                    float(k["pnl_r_sum_eff"]),
                    ts,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(rows)


__all__ = [
    "BaseLearner",
    "ClosedTrade",
    "DEFAULT_MAX_HOLD_FALLBACK_BARS",
    "HourlyCommitReport",
    "LEARNER_DELTA_HOURLY_CAP",
    "LEARNER_INDIVIDUAL_MULT_CLIP",
    "LEARNER_MIN_NEFF_FOR_DELTA",
    "LEARNER_PRODUCT_CLIP",
    "LEARNER_SNAPSHOT_DIR",
    "LearnerMultResult",
    "NEUTRAL_MULT",
    "TRIPLE_BLOCK_DURATION_SEC",
    "TRIPLE_BLOCK_SIZE_MULT",
    "TripleBlockEntry",
    "WR_DEMOTE_THRESHOLD",
    "WR_PROMOTE_THRESHOLD",
    "clip_hourly_delta",
    "clip_individual_mult",
    "clip_product_mult",
    "evaluate_triple_block",
    "evict_expired_triple_blocks",
    "resolve_final_size_mult",
    "restore_snapshot_from_disk",
]
