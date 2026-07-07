"""Migration — re-base ``pnl_r`` onto per-trade staked risk (``risk_usd``).

DEMO/PAPER only; virtual funds. MANDATE-SAFE, /debate-cleared (2026-07-07):
``sign(pnl_r) == sign(pnl_usd)`` preserved, the dollar (``fills.pnl_usd``)
truth is UNTOUCHED, this UN-CRUSHES measurement (never throttles/blocks/
rejects), and T4 sizing is byte-identical (sizing keys off the stop-distance
SSOT + ``r_budget_for_venue`` — a SEPARATE constant — never ``pnl_r``).

Canonical fix (forward code already swapped — see ``_production_close.py`` /
``_production_close_helpers.py``): ``pnl_r ≡ realized_usd / risk_usd_at_entry``
(per-trade dollar staked risk, ``positions.risk_usd``) REPLACES the per-stream
``R_budget`` ledger (``realised_r_stream``) as the realised-R denominator. This
tool backfills every historical CLOSED position onto the new ruler.

Sequence (ORDER IS LOAD-BEARING, per /debate):
  (a) FX RE-DERIVE  — historical Capital closed-row ``risk_usd`` computed with
      a quote→USD rate (a non-USD-quoted epic — USDJPY, J225=JPY, EU50=EUR —
      stamped WITHOUT one is inflated/deflated by the raw FX level, audit rank
      4: J225 risk_usd $47,615.89 vs real ≈$317). Uses a RECENT rate proxy (FX
      moves a few % vs the 150x defect magnitude — acceptable, documented,
      overridable via ``--rate-override CCY=RATE``). OKX (always USD-quoted)
      and USD-quoted Capital epics are untouched.
  (b) BACKFILL pnl_r = realized_usd / risk_usd for every CLOSED position with
      risk_usd > 0 (realized_usd = SUM(fills.pnl_usd) WHERE
      contribution_id=position_id AND is_close=1), mirrored onto
      position_strategy_segments.pnl_r and (best-effort) probe_decisions.
      realized_pnl_r. Positions with NULL/0 risk_usd (the 2 reconciled-zombie
      rows) are EXCLUDED, never fabricated.
  (c) RESET cell_matrix (+shadow-context) / learner_state / learner_blocks /
      learner_posterior — EVERY historical R changes under the new ruler (not
      just the FX-affected Capital rows: OKX pnl_r also differs, per-trade
      risk_usd vs the flat per-stream R_budget), so blending old+new EWMA
      would corrupt the aggregates. A full DELETE+rebuild is the only clean
      option — NO blended old+new EWMA.
  (d) REPLAY every corrected close (chronological, ``closed_ts`` ASC) through
      the SAME three fold surfaces the live close path drives —
      ``update_on_trade_close`` (cell matrix), ``LearnerScheduler`` (learner
      aggregates), ``maybe_update_posterior`` (edge-validation posterior) —
      using the SAME real-fee NET R the live path folds (``compute_net_pnl_r``,
      unmodified) so the rebuilt aggregates are fold-for-fold consistent with
      what a live close would have produced under the new ruler.

Idempotent: every step is a pure RECOMPUTE-AND-OVERWRITE from truth
(``fills.pnl_usd`` + ``positions.risk_usd``), never an incremental accumulate —
re-running the whole migration reproduces the identical end state. Guard:
refuses to run (``--apply``) while the live bot lock is held (pidfile + alive
process) — the caller must stop the bot first; this tool never signals it.

Usage
-----
    python3 -m tools.rebase_pnl_r_to_risk_usd --db data/polaris_live.sqlite
    python3 -m tools.rebase_pnl_r_to_risk_usd --db data/polaris_live.sqlite \\
        --probe-db data/paper/probes.sqlite --apply
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from dataclasses import dataclass, field

from polaris.core.lifecycle.trade import SimulatedTrade
from polaris.core.metrics.risk_unit import (
    STOP_ATR_MULT,
    realised_r,
    risk_usd_at_entry,
)
from polaris.scripts._production_capital_sizing import _bars_quote_usd_rate
from polaris.scripts._production_close_effects import (
    _safe_update_cell_matrix,
    _safe_update_posterior,
    compute_net_pnl_r,
)
from polaris.scripts._production_state import ProdLoopState
from polaris.scripts.dashboard.snapshot_q_common import _quote_ccy_for_symbol
from tools.ops.botctl import pid_alive, read_pidfile
from tools.ops.ops_config import OpsConfig

logger = logging.getLogger("rebase_pnl_r_to_risk_usd")

# Learner-fold session bucketing needs a live-venue-session resolver; imported
# lazily inside _replay_learners to avoid pulling ProdLoopState-heavy modules
# into every caller of this file (matches the existing scripts-layer pattern).

_CELL_MATRIX_TABLES: tuple[str, ...] = (
    "cell_matrix_p0",
    "cell_matrix_parent3",
    "cell_matrix_parent2",
    "cell_matrix_shadow_context",
)
_LEARNER_TABLES: tuple[str, ...] = (
    "learner_state",
    "learner_blocks",
    "learner_snapshot",
    "learner_posterior",
)


class BotLockHeldError(RuntimeError):
    """Raised when ``--apply`` is attempted while the live bot pidfile is alive."""


@dataclass(frozen=True, slots=True)
class FxRederiveResult:
    position_id: str
    venue: str
    symbol: str
    quote_ccy: str
    old_risk_usd: float | None
    new_risk_usd: float | None
    skipped_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BackfillResult:
    position_id: str
    venue: str
    old_pnl_r: float | None
    new_pnl_r: float
    realized_usd: float
    risk_usd: float


@dataclass(slots=True)
class MigrationReport:
    fx_rederived: list[FxRederiveResult] = field(default_factory=list)
    backfilled: list[BackfillResult] = field(default_factory=list)
    excluded_null_risk: list[str] = field(default_factory=list)
    cell_matrix_reset_rows: int = 0
    learner_reset_rows: int = 0
    replayed: int = 0


def assert_bot_not_running(cfg: OpsConfig | None = None) -> None:
    """Guard: raise if the live paper-loop pidfile names a still-alive PID.

    The caller (an operator) must ``./scripts/stop_bot.sh`` first — this tool
    never signals the bot itself (out of scope, and a migration must never
    race a live writer).
    """
    cfg = cfg or OpsConfig.default()
    pid = read_pidfile(cfg)
    if pid is not None and pid_alive(pid):
        raise BotLockHeldError(
            f"live bot pidfile={cfg.pidfile} pid={pid} is ALIVE — stop the bot "
            "first (./scripts/stop_bot.sh); refusing to migrate a live DB"
        )


# ---------------------------------------------------------------------------
# (a) FX re-derive — Capital closed-row risk_usd, quote_ccy corrected
# ---------------------------------------------------------------------------


def _entry_fill(
    conn: sqlite3.Connection, *, position_id: str, instrument_id: str,
) -> tuple[float, float] | None:
    """``(entry_price, base_qty)`` of the entry fill, or ``None``."""
    row = conn.execute(
        "SELECT fill_price, base_qty FROM fills WHERE contribution_id = ? "
        "AND instrument_id = ? AND is_close = 0 ORDER BY ts_ms ASC LIMIT 1",
        (position_id, instrument_id),
    ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None
    price, qty = float(row[0]), float(row[1])
    return (price, qty) if price > 0.0 and qty > 0.0 else None


def compute_fx_rederive(
    conn: sqlite3.Connection, *, rate_override: dict[str, float] | None = None,
) -> list[FxRederiveResult]:
    """Read every CLOSED position and compute its FX-corrected ``risk_usd``.

    Pure read. Skips (with a reason) a USD-quoted row (nothing to fix) or one
    whose conversion rate is unresolvable (no bars for the pair AND no
    ``rate_override`` entry) — NEVER guessed. ``rate_override`` (CCY -> rate)
    lets an operator supply a known-good rate when bars are unavailable
    offline; documented, not a silent default.
    """
    rate_override = rate_override or {}
    rows = conn.execute(
        "SELECT position_id, venue, symbol, entry_atr_pct, risk_usd "
        "FROM positions WHERE status = 'closed'"
    ).fetchall()
    out: list[FxRederiveResult] = []
    for position_id, venue, symbol, entry_atr_pct, old_risk_usd in rows:
        if str(venue).lower() != "capital":
            continue  # OKX is always USD-quoted (USDT pairs) — nothing to fix.
        quote_ccy = _quote_ccy_for_symbol(venue, symbol, "USD")
        if quote_ccy in ("", "USD"):
            continue  # already USD-quoted — no FX defect possible.
        entry = _entry_fill(conn, position_id=position_id, instrument_id=f"{venue}:{symbol}")
        if entry is None:
            out.append(FxRederiveResult(
                position_id=position_id, venue=venue, symbol=symbol,
                quote_ccy=quote_ccy, old_risk_usd=old_risk_usd, new_risk_usd=None,
                skipped_reason="no entry fill / qty",
            ))
            continue
        entry_price, base_qty = entry
        rate = rate_override.get(quote_ccy) or _bars_quote_usd_rate(conn, quote_ccy)
        if rate is None or rate <= 0.0:
            out.append(FxRederiveResult(
                position_id=position_id, venue=venue, symbol=symbol,
                quote_ccy=quote_ccy, old_risk_usd=old_risk_usd, new_risk_usd=None,
                skipped_reason=f"no {quote_ccy}->USD rate (no conversion-pair bars, no override)",
            ))
            continue
        new_risk_usd = risk_usd_at_entry(
            entry_price=entry_price,
            entry_atr_pct=float(entry_atr_pct) if entry_atr_pct is not None else 0.0,
            base_qty=base_qty, stop_atr_mult=STOP_ATR_MULT, quote_usd_rate=rate,
        )
        out.append(FxRederiveResult(
            position_id=position_id, venue=venue, symbol=symbol,
            quote_ccy=quote_ccy, old_risk_usd=old_risk_usd, new_risk_usd=new_risk_usd,
        ))
    return out


def apply_fx_rederive(conn: sqlite3.Connection, results: list[FxRederiveResult]) -> int:
    n = 0
    for r in results:
        if r.skipped_reason is not None or r.new_risk_usd is None:
            continue
        conn.execute(
            "UPDATE positions SET risk_usd = ? WHERE position_id = ?",
            (r.new_risk_usd, r.position_id),
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# (b) Backfill pnl_r = realized_usd / risk_usd
# ---------------------------------------------------------------------------


def compute_backfill(conn: sqlite3.Connection) -> tuple[list[BackfillResult], list[str]]:
    """Corrected ``pnl_r`` for every CLOSED position with ``risk_usd > 0``.

    Returns ``(results, excluded_position_ids)`` — excluded rows are CLOSED
    positions with NULL/0 ``risk_usd`` (the reconciled-zombie rows), left
    untouched (never fabricated a risk unit).
    """
    rows = conn.execute(
        "SELECT position_id, venue, symbol, pnl_r, risk_usd "
        "FROM positions WHERE status = 'closed'"
    ).fetchall()
    results: list[BackfillResult] = []
    excluded: list[str] = []
    for position_id, venue, _symbol, old_pnl_r, risk_usd in rows:
        if risk_usd is None or float(risk_usd) <= 0.0:
            excluded.append(position_id)
            continue
        realized_usd = float(
            conn.execute(
                "SELECT COALESCE(SUM(pnl_usd), 0.0) FROM fills "
                "WHERE contribution_id = ? AND is_close = 1",
                (position_id,),
            ).fetchone()[0]
        )
        new_pnl_r = realised_r(pnl_usd=realized_usd, risk_usd=float(risk_usd))
        results.append(BackfillResult(
            position_id=position_id, venue=venue, old_pnl_r=old_pnl_r,
            new_pnl_r=new_pnl_r, realized_usd=realized_usd, risk_usd=float(risk_usd),
        ))
    return results, excluded


def apply_backfill(
    conn: sqlite3.Connection, results: list[BackfillResult], *,
    probe_conn: sqlite3.Connection | None = None,
) -> int:
    """Stamp ``positions.pnl_r`` + mirror onto segments / (best-effort) probe."""
    n = 0
    for r in results:
        conn.execute(
            "UPDATE positions SET pnl_r = ? WHERE position_id = ?",
            (r.new_pnl_r, r.position_id),
        )
        conn.execute(
            "UPDATE position_strategy_segments SET pnl_r = ? WHERE position_id = ?",
            (r.new_pnl_r, r.position_id),
        )
        if probe_conn is not None:
            try:
                probe_conn.execute(
                    "UPDATE probe_decisions SET realized_pnl_r = ? WHERE position_id = ?",
                    (r.new_pnl_r, r.position_id),
                )
            except sqlite3.Error as exc:  # fail-open — sidecar telemetry only
                logger.warning(
                    "[migrate] probe mirror failed pos=%s: %r", r.position_id, exc,
                )
        n += 1
    return n


# ---------------------------------------------------------------------------
# (c) Reset cell_matrix + learner aggregates (no blended old+new EWMA)
# ---------------------------------------------------------------------------


def reset_learned_aggregates(conn: sqlite3.Connection) -> tuple[int, int]:
    """DELETE every cell_matrix / learner aggregate row. Returns (cell, learner) counts."""
    cell_n = 0
    for t in _CELL_MATRIX_TABLES:
        cell_n += conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        conn.execute(f"DELETE FROM {t}")
    learner_n = 0
    for t in _LEARNER_TABLES:
        learner_n += conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        conn.execute(f"DELETE FROM {t}")
    return cell_n, learner_n


# ---------------------------------------------------------------------------
# (d) Replay corrected closes through the live fold surfaces
# ---------------------------------------------------------------------------


def replay_corrected_closes(
    conn: sqlite3.Connection, results: list[BackfillResult],
) -> int:
    """Fold every corrected close through cell_matrix + learners + posterior.

    Chronological (``closed_ts`` ASC) so EWMA decay history is correct.
    Reuses the SAME live fold helpers (``_safe_update_cell_matrix`` /
    ``LearnerScheduler`` / ``_safe_update_posterior`` / ``compute_net_pnl_r``,
    all unmodified) so the rebuilt aggregates are fold-for-fold identical to
    what a live close under the new ruler would have produced. Fail-open per
    position (mirrors the live close path) — one bad row must not abort the
    whole rebuild.
    """
    from polaris.core.learners import ClosedTrade, LearnerScheduler
    from polaris.core.sizing.session import resolve_venue_session

    by_pid = {r.position_id: r for r in results}
    rows = conn.execute(
        "SELECT position_id, venue, symbol, strategy_id, closed_ts, "
        "COALESCE(entry_regime, 'chop') "
        "FROM positions WHERE status = 'closed' AND closed_ts IS NOT NULL "
        "ORDER BY closed_ts ASC, position_id ASC"
    ).fetchall()
    n = 0
    for position_id, venue, symbol, strategy_id, closed_ts, regime in rows:
        result = by_pid.get(position_id)
        if result is None:
            continue  # excluded (NULL/0 risk_usd) — never folded.
        now_ts = int(closed_ts)
        trade = SimulatedTrade(
            signal_id=position_id, venue=str(venue), symbol=str(symbol),
            strategy_id=str(strategy_id), side="long", entry_price=0.0,
            notional_usd=0.0, open_ts=now_ts, position_id=position_id,
        )
        try:
            _pnl_usd_net, pnl_r_net = compute_net_pnl_r(
                conn, trade=trade, gross_pnl_r=result.new_pnl_r,
                gross_pnl_usd=result.realized_usd,
            )
            won = pnl_r_net > 0.0
            state = ProdLoopState()
            _safe_update_cell_matrix(
                conn, trade=trade, regime=str(regime), pnl_r=pnl_r_net,
                won=won, now_ts=now_ts, state=state,
            )
            sched = LearnerScheduler(conn, expected_holding_bars=20)
            closed_record = ClosedTrade(
                trade_id=trade.signal_id, strategy_id=trade.strategy_id,
                ticker=trade.symbol, venue=trade.venue, regime=str(regime),
                session=resolve_venue_session(trade.venue, now_ts),
                pnl_r=pnl_r_net, won=won, holding_bars=20, closed_ts=now_ts,
            )
            for learner in sched.learners:
                learner.update(closed_record, now_ts=now_ts)
            _safe_update_posterior(
                conn, trade=trade, regime=str(regime), pnl_r_net=pnl_r_net,
                pnl_r_gross=result.new_pnl_r, now_ts=now_ts,
            )
            # ``learner.update`` (base.py) writes via an IMPLICIT transaction it
            # never commits (the live tick loop's NEXT unrelated commit happens
            # to flush it over real wall-clock gaps between closes). Replaying
            # 183 closes back-to-back has no such gap — an uncommitted implicit
            # transaction here would make the NEXT row's ``update_on_trade_close``
            # (its own ``BEGIN IMMEDIATE``) raise "cannot start a transaction
            # within a transaction", which its fail-open swallows — silently
            # folding NOTHING. Commit after every fully-folded position closes
            # that window.
            if conn.in_transaction:
                conn.commit()
            n += 1
        except Exception as exc:  # noqa: BLE001 — fail-open, one row must not abort rebuild
            logger.warning("[migrate] replay fold failed pos=%s: %r", position_id, exc)
            if conn.in_transaction:
                conn.rollback()
    return n


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_migration(
    conn: sqlite3.Connection, *, apply: bool, probe_conn: sqlite3.Connection | None = None,
    rate_override: dict[str, float] | None = None,
) -> MigrationReport:
    """Run the full (a)->(b)->(c)->(d) sequence. Order is load-bearing.

    ``apply=False`` (default): dry-run — computes every step's result set
    (steps compose read-then-plan) but writes nothing. ``apply=True`` commits
    steps (a)+(b)+(c) in one transaction, THEN commits, THEN runs (d) — every
    fold helper in (d) (``update_on_trade_close`` / the learner network /
    ``maybe_update_posterior``) self-transacts its own ``BEGIN IMMEDIATE``, so
    (d) MUST start with no ambient open transaction (nesting raises
    ``OperationalError: cannot start a transaction within a transaction``,
    which the fold helpers fail-open swallow — silently folding NOTHING).
    Idempotent either way (pure recompute-and-overwrite, never accumulate).
    """
    report = MigrationReport()
    # (a) FX re-derive — MUST run before (b) reads risk_usd, else a
    # non-USD Capital row backfills pnl_r off the still-FX-defective risk_usd.
    report.fx_rederived = compute_fx_rederive(conn, rate_override=rate_override)
    if apply:
        apply_fx_rederive(conn, report.fx_rederived)
    # (b) backfill — reads risk_usd AFTER (a)'s corrections are visible in
    # this same connection/transaction.
    backfilled, excluded = compute_backfill(conn)
    report.backfilled = backfilled
    report.excluded_null_risk = excluded
    if apply:
        apply_backfill(conn, backfilled, probe_conn=probe_conn)
        # (c) reset — every historical R changed, so old+new EWMA must never blend.
        cell_n, learner_n = reset_learned_aggregates(conn)
        report.cell_matrix_reset_rows = cell_n
        report.learner_reset_rows = learner_n
        # Commit (a)+(b)+(c) BEFORE (d) — see docstring: the fold helpers below
        # self-transact and must start with no ambient open transaction.
        conn.commit()
        if probe_conn is not None:
            probe_conn.commit()
        # (d) replay — rebuild from the corrected pnl_r, chronological order.
        report.replayed = replay_corrected_closes(conn, backfilled)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-base pnl_r onto per-trade risk_usd (DEMO/PAPER migration)."
    )
    parser.add_argument("--db", required=True, help="path to the live sqlite DB")
    parser.add_argument("--probe-db", help="path to the probes.sqlite sidecar (optional)")
    parser.add_argument(
        "--rate-override", action="append", default=[],
        help="CCY=RATE quote->USD override, e.g. JPY=0.0067 (repeatable)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="write the migration (default: dry-run report only)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    rate_override: dict[str, float] = {}
    for pair in args.rate_override:
        ccy, _, rate = pair.partition("=")
        if ccy and rate:
            rate_override[ccy.upper()] = float(rate)

    if args.apply:
        assert_bot_not_running()

    conn = sqlite3.connect(args.db, timeout=30.0)
    probe_conn = sqlite3.connect(args.probe_db, timeout=30.0) if args.probe_db else None
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        # No ambient outer transaction: dry-run (apply=False) never writes
        # (compute_* are pure reads); apply=True manages its own transaction
        # boundaries internally (run_migration commits (a)+(b)+(c) before the
        # self-transacting (d) fold helpers run — see its docstring).
        report = run_migration(
            conn, apply=args.apply, probe_conn=probe_conn, rate_override=rate_override,
        )
        resolved_fx = [r for r in report.fx_rederived if r.skipped_reason is None]
        skipped_fx = [r for r in report.fx_rederived if r.skipped_reason is not None]
        logger.info(
            "[migrate] (a) FX re-derive: %d resolved, %d skipped", len(resolved_fx), len(skipped_fx),
        )
        for r in skipped_fx:
            logger.warning(
                "[migrate] SKIP FX %s %s:%s (%s) — %s",
                r.position_id, r.venue, r.symbol, r.quote_ccy, r.skipped_reason,
            )
        logger.info(
            "[migrate] (b) backfill: %d position(s) %s, %d excluded (NULL/0 risk_usd)",
            len(report.backfilled), "updated" if args.apply else "would update",
            len(report.excluded_null_risk),
        )
        if args.apply:
            logger.info(
                "[migrate] (c) reset: %d cell_matrix rows, %d learner rows cleared",
                report.cell_matrix_reset_rows, report.learner_reset_rows,
            )
            logger.info("[migrate] (d) replay: %d close(s) refolded", report.replayed)
            logger.info("[migrate] APPLIED")
        else:
            logger.info("[migrate] DRY-RUN — pass --apply to write (bot must be stopped)")
    finally:
        conn.close()
        if probe_conn is not None:
            probe_conn.close()


if __name__ == "__main__":
    main()
