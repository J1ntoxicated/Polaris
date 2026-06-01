"""Day 8 — production close-path post-trade fan-out (fault-isolated effects).

Auxiliary fail-safe helpers split out of ``_production_close`` to keep both
modules ≤500 LOC. Each ``_safe_*`` helper wraps one post-close side effect
(fault record / regime lookup / Layer 4 cell-matrix / Layer 5 learners / G8
reflector) so a failure in one never aborts the close transaction. Imported +
called by ``_production_close._close_trade_with_real_pnl``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any

from polaris.core.cell_matrix import (
    CellContext,
    CellKeyP0,
    TradeClose,
    update_on_trade_close,
)
from polaris.core.isolation.circuit_breaker import (
    FAULT_EXCEPTION,
    record_fault,
)
from polaris.core.learners import ClosedTrade, LearnerScheduler
from polaris.core.learners.meta_label import (
    compute_triple_barrier_label,
    persist_meta_label,
)
from polaris.core.learners.posterior import (
    cost_adjusted_pnl_r,
    maybe_update_posterior,
)
from polaris.core.pipeline.agents import post_trade_reflector_gate
from polaris.core.pipeline.agents._gpt_client import (
    GPT_P0_MODEL,
    GPT_P1_MODEL,
)
from polaris.core.pipeline.gate_orchestrator import log_gate_event
from polaris.core.pipeline.gate_state import (
    GATE_POST_TRADE_REFLECTOR,
    GateContext,
    SignalLifecycle,
)
from polaris.core.streams import resolve_stream, resolve_stream_profile
from polaris.scripts._production_bars import BAR_TS_CLOCK_SKEW_SLACK_SEC
from polaris.scripts._smoke_fills import SimulatedTrade

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)

# FIX 1 — minimum ATR-pct floor for the cost-adjusted R-denominator. The old
# fixed 1e-6 atr_usd floor conflated "missing bars" with "low-price symbol"
# (ALGO ~$0.12): the WHOLE-POSITION cost_usd divided by the per-UNIT atr_usd
# (~1e-6) exploded pnl_r_net to ~-210000 R. The R-denominator is now the
# whole-position 1R dollar value (``size_usd × atr_pct × 2``) with ``atr_pct``
# floored at MIN_ATR_PCT so a flat bar window cannot drive it to ~0. A truly
# degenerate entry_price (<=0) yields a ``None`` sentinel and the caller SKIPS
# the cost-in-R adjustment entirely (measurement-only — never gates sizing).
MIN_ATR_PCT = 0.001


def _safe_record_fault(
    conn: sqlite3.Connection, *, strategy_id: str, phase: str, exc: BaseException,
    now_ts: int, state: ProdLoopState,
) -> None:
    """Record a fault without ever propagating an exception out of this call."""
    try:
        record_fault(
            conn, strategy_id=strategy_id, fault_type=FAULT_EXCEPTION,
            now_ts=now_ts,
            detail={"phase": phase, "exc": str(exc)[:200]},
        )
        state.fault_events += 1
    except Exception as inner:  # noqa: BLE001 — fault recording is best-effort
        logger.error(
            "[fault] record_fault itself failed phase=%s strategy=%s: %r",
            phase, strategy_id, inner,
        )


def _safe_lookup_regime(
    lookup_regime: Any, conn: sqlite3.Connection, trade: SimulatedTrade,
) -> str:
    try:
        return str(lookup_regime(conn, trade.venue, trade.symbol))
    except Exception as exc:  # noqa: BLE001 — fail-open to chop
        logger.error("[L6] lookup_regime raised: %r", exc)
        return "chop"


def _safe_update_cell_matrix(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
) -> None:
    try:
        update_on_trade_close(
            conn,
            trade=TradeClose(
                key=CellKeyP0(
                    exchange=trade.venue, strategy=trade.strategy_id,
                    ticker=trade.symbol, regime=regime,
                ),
                context=CellContext(
                    # Stream SSOT (design §2.1): okx→spot, capital→cfd, identical
                    # to the prior venue-binary literal.
                    group=resolve_stream(trade.venue).product_class,
                    session="asia", direction=trade.side,
                    liquidity_tier="top",
                ),
                pnl_r=pnl_r, won=won, closed_ts=now_ts,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.error("[L4] cell matrix update failed: %r", exc)
        _safe_record_fault(
            conn, strategy_id=trade.strategy_id,
            phase="cell_matrix_update", exc=exc, now_ts=now_ts, state=state,
        )


def _safe_run_learners(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
) -> None:
    try:
        sched = LearnerScheduler(conn, expected_holding_bars=20)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.error("[L5] LearnerScheduler init raised: %r", exc)
        _safe_record_fault(
            conn, strategy_id=trade.strategy_id,
            phase="learner_init", exc=exc, now_ts=now_ts, state=state,
        )
        return
    closed_record = ClosedTrade(
        trade_id=trade.signal_id, strategy_id=trade.strategy_id,
        ticker=trade.symbol, venue=trade.venue, regime=regime,
        session="asia", pnl_r=pnl_r, won=won, holding_bars=20, closed_ts=now_ts,
    )
    for learner in sched.learners:
        try:
            learner.update(closed_record, now_ts=now_ts)
        except Exception as exc:  # noqa: BLE001 — fault-isolate per learner
            logger.error(
                "[L5] learner %s update raised: %r",
                getattr(learner, "learner_id", "<unknown>"), exc,
            )
            _safe_record_fault(
                conn, strategy_id=trade.strategy_id,
                phase=f"learner_update_{getattr(learner, 'learner_id', 'unknown')}",
                exc=exc, now_ts=now_ts, state=state,
            )


def _safe_record_meta_label(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
    expected_holding_bars: int = 20,
) -> None:
    """Meta-labeling (#10) — record the triple-barrier label for this close.

    Collection-only: writes one ``meta_labels`` row per closed trade and never
    gates sizing/exits (AGGRESSIVE bias preserved). Fail-open — a label failure
    must never abort an already-committed close. ``holding_bars`` is derived
    from elapsed wall-clock against 1-minute bars (the loop's bar cadence).
    """
    try:
        elapsed_sec = max(0, now_ts - trade.open_ts)
        holding_bars = elapsed_sec // 60
        label = compute_triple_barrier_label(
            pnl_r=pnl_r, won=won, holding_bars=holding_bars,
            expected_holding_bars=expected_holding_bars,
        )
        persist_meta_label(
            conn, trade_id=trade.signal_id, strategy_id=trade.strategy_id,
            venue=trade.venue, ticker=trade.symbol, regime=regime,
            session="asia", label=label, now_ts=now_ts,
        )
        state.meta_labels += 1
    except Exception as exc:  # noqa: BLE001 — collection-only side effect, fail-open
        logger.warning(
            "[meta-label] label record failed %s:%s: %r",
            trade.venue, trade.symbol, exc,
        )


def _read_cost_inputs(
    conn: sqlite3.Connection, trade: SimulatedTrade
) -> tuple[float, float, float, float, float, float | None]:
    """Read (entry_fee, entry_slip, exit_fee, exit_slip, size_usd, atr_usd).

    P0-2 fix — BOTH legs are matched by ``contribution_id = position_id`` (the
    close fill is persisted with ``contribution_id=trade.position_id`` in
    ``_close_trade_with_real_pnl``). This stops a sibling position on the same
    (strategy, instrument) from having its close fee/slippage cross-applied.
    Only when ``position_id`` is unset (legacy callers) do we fall back to the
    latest ``(strategy, instrument)`` fill. ``atr_usd`` mirrors the
    R-denominator used by the gross calc so ``pnl_r_net = pnl_r -
    cost_usd/atr_usd`` is consistent.

    FIX 1 — the returned ``atr_usd`` is the WHOLE-POSITION 1R dollar value
    (``size_usd × atr_pct × 2``, atr_pct floored at ``MIN_ATR_PCT``) — the right
    denominator for the whole-position ``cost_usd``. The old per-UNIT
    ``entry_price × atr_pct × 2`` floored at ``1e-6`` blew up cost-in-R for
    low-price symbols. A truly degenerate entry_price (<=0) returns ``None`` so
    the caller skips the cost adjustment.
    """
    inst = f"{trade.venue}:{trade.symbol}"
    entry: tuple[Any, ...] | None = None
    exit_row: tuple[Any, ...] | None = None
    if trade.position_id:
        entry = conn.execute(
            "SELECT fee_usd, slippage_bps, size_usd, fill_price FROM fills "
            "WHERE contribution_id = ? AND is_close = 0 ORDER BY ts_ms ASC LIMIT 1",
            (trade.position_id,),
        ).fetchone()
        exit_row = conn.execute(
            "SELECT fee_usd, slippage_bps FROM fills "
            "WHERE contribution_id = ? AND is_close = 1 ORDER BY ts_ms DESC LIMIT 1",
            (trade.position_id,),
        ).fetchone()
    if entry is None:
        entry = conn.execute(
            "SELECT fee_usd, slippage_bps, size_usd, fill_price FROM fills "
            "WHERE strategy_id = ? AND instrument_id = ? AND is_close = 0 "
            "ORDER BY ts_ms DESC LIMIT 1",
            (trade.strategy_id, inst),
        ).fetchone()
    if exit_row is None:
        exit_row = conn.execute(
            "SELECT fee_usd, slippage_bps FROM fills "
            "WHERE strategy_id = ? AND instrument_id = ? AND is_close = 1 "
            "ORDER BY ts_ms DESC LIMIT 1",
            (trade.strategy_id, inst),
        ).fetchone()
    entry_fee = float(entry[0]) if entry else 0.0
    entry_slip = float(entry[1]) if entry else 0.0
    size_usd = float(entry[2]) if entry else trade.notional_usd
    entry_price = float(entry[3]) if entry else trade.entry_price
    exit_fee = float(exit_row[0]) if exit_row else 0.0
    exit_slip = float(exit_row[1]) if exit_row else 0.0
    # Exclude FUTURE-dated bars (stale +10h Capital) from the cost-R ATR window
    # so the R-denominator reflects real recent volatility, not a +10h ghost bar.
    ts_upper = int(time.time()) + BAR_TS_CLOCK_SKEW_SLACK_SEC
    bar_rows = conn.execute(
        "SELECT close, high, low FROM bars WHERE instrument_id = ? "
        "AND bar_interval = '1m' AND ts <= ? ORDER BY ts DESC LIMIT 14",
        (inst, ts_upper),
    ).fetchall()
    atr_pct_samples = [
        (float(r[1]) - float(r[2])) / float(r[0]) for r in bar_rows if float(r[0]) > 0.0
    ]
    atr_pct = sum(atr_pct_samples) / len(atr_pct_samples) if atr_pct_samples else 0.005
    # FIX 1 — the R-denominator that ``cost_usd`` (a WHOLE-POSITION dollar cost)
    # is divided by must itself be the WHOLE-POSITION 1R dollar value, not the
    # per-UNIT ATR. The gross pnl_r divides a per-unit price move by the per-unit
    # ATR (dimensionless); dividing the whole-position cost_usd by the per-unit
    # ATR (the prior bug) scaled by base_qty (~size_usd/entry_price), which for a
    # low-price symbol (ALGO ~$0.12, 5000 coins) blew the cost-in-R into the
    # thousands of R. Position 1R = base_qty × per_unit_atr = (size_usd/price) ×
    # (price × atr_pct × 2) = size_usd × atr_pct × 2 — independent of price, so a
    # cheap coin no longer explodes. ``atr_pct`` floors at MIN_ATR_PCT so a flat
    # bar window cannot drive the denominator to ~0.
    if entry_price > 0.0:
        atr_usd: float | None = abs(size_usd) * max(atr_pct, MIN_ATR_PCT) * 2.0
    else:
        # Truly degenerate (entry_price missing/<=0) → sentinel; caller skips the
        # cost-in-R adjustment (net==gross) rather than feed a garbage net-R.
        atr_usd = None
    return entry_fee, entry_slip, exit_fee, exit_slip, size_usd, atr_usd


def _safe_update_posterior(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, pnl_usd: float, now_ts: int,
) -> None:
    """Edge-validation Phase 1 — fold the cost-adjusted R into the bucket
    posterior. Fail-open (``logger.warning``): a posterior failure must never
    abort an already-committed close. This table is never read by sizing.
    """
    try:
        (
            entry_fee, entry_slip, exit_fee, exit_slip, size_usd, atr_usd,
        ) = _read_cost_inputs(conn, trade)
        if atr_usd is None:
            # FIX 1 — degenerate R-denominator (entry_price <=0): skip the
            # cost-in-R adjustment (net==gross) so the NIG posterior never folds
            # |net_r| ≫ |gross_r|. WARN (not silent) per the no-garbage mandate.
            logger.warning(
                "[edge-validation] %s:%s degenerate atr_usd (entry_price<=0) — "
                "cost-in-R adjustment skipped (net==gross, no posterior blow-up)",
                trade.venue, trade.symbol,
            )
        _pnl_usd_net, pnl_r_net = cost_adjusted_pnl_r(
            gross_pnl_usd=pnl_usd, gross_pnl_r=pnl_r, size_usd=size_usd,
            atr_usd=atr_usd, venue=trade.venue,
            entry_fee_usd=entry_fee, exit_fee_usd=exit_fee,
            entry_slippage_bps=entry_slip, exit_slippage_bps=exit_slip,
        )
        maybe_update_posterior(
            conn, exchange=trade.venue, strategy=trade.strategy_id,
            ticker=trade.symbol, regime=regime, pnl_r_net=pnl_r_net, now_ts=now_ts,
        )
        # Posterior fold (INFO): the cost-adjusted R observation folded into the
        # NIG bucket (exchange×strategy×ticker×regime) at close — the edge-
        # validation 거동 record. gross pnl_r vs net (after fees+slippage). This
        # table is never read by sizing; log only, no decision changed.
        logger.info(
            "[edge-validation] posterior %s:%s strategy=%s regime=%s "
            "pnl_r_gross=%.3f pnl_r_net=%.3f",
            trade.venue, trade.symbol, trade.strategy_id, regime,
            pnl_r, pnl_r_net,
        )
    except Exception as exc:  # noqa: BLE001 — measure-only side effect, fail-open
        logger.warning(
            "[edge-validation] posterior update failed %s:%s: %r",
            trade.venue, trade.symbol, exc,
        )


async def _safe_run_g8(
    conn: sqlite3.Connection, *, trade: SimulatedTrade, regime: str,
    pnl_r: float, won: bool, now_ts: int, state: ProdLoopState,
    gpt_client: Any | None = None, phase: str = "P0",
) -> None:
    g8_ctx = GateContext(
        run_id=uuid.uuid4().hex, signal_id=trade.signal_id,
        position_id=trade.position_id or f"pos_{trade.signal_id[:10]}",
        gate_id=GATE_POST_TRADE_REFLECTOR,
        venue=trade.venue, symbol=trade.symbol,
        strategy_id=trade.strategy_id,
        payload={
            "closed_trade": {
                "trade_id": trade.signal_id,
                "strategy_id": trade.strategy_id,
                "regime": regime, "session": "asia",
                "pnl_r": pnl_r, "won": won,
            },
            "closed_trade_count": len(state.closed_trades),
        },
        started_ts=now_ts, state=SignalLifecycle.CLOSED,
        # Gate architecture Phase 0: per-stream seam (read-but-no-decision in P0).
        stream_profile=resolve_stream_profile(trade.venue),
    )
    # codex 2026-05-07 P1.4 fix: phase-aware G8 dispatch so the production
    # paper harness can exercise the GPT P1 lesson branch (was hardcoded
    # ``client=None``, which silently kept G8 on the Python template even
    # when phase=="P1"). ADR-004 §Phase invariant honoured by passing
    # ``GPT_P0_MODEL`` (unused, client=None) at P0 vs. ``GPT_P1_MODEL`` +
    # gpt_client at P1.
    if phase == "P1" and gpt_client is not None:
        client_arg: Any = gpt_client
        model_arg = GPT_P1_MODEL
    else:
        client_arg = None
        model_arg = GPT_P0_MODEL
    try:
        g8_result = await post_trade_reflector_gate(
            g8_ctx, client=client_arg, conn=conn, model=model_arg,
        )
        log_gate_event(conn, g8_ctx, g8_result)
        state.g8_runs += 1
    except Exception as exc:  # noqa: BLE001 — G8 must not drop a closed trade
        logger.error("[G8] reflector raised: %r", exc)
        _safe_record_fault(
            conn, strategy_id=trade.strategy_id,
            phase="g8_reflector", exc=exc, now_ts=now_ts, state=state,
        )
