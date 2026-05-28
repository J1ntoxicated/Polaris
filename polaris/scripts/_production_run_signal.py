"""Day 8 — production per-signal G1→G8 pipeline driver.

Drives one validated RawSignal through gates 1-7 with production-shaped payloads
(``build_*_payload``), invoking the injected AllocatorFence submit closure and
real G6/G7 mark-to-market. Split out of ``_production_pipeline`` to keep both
modules ≤500 LOC; ``_production_pipeline`` re-exports ``run_pipeline_for_signal``
so existing import paths keep working.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from polaris.core.pipeline import (
    GateOrchestrator,
    build_exit_payload,
    build_monitor_payload,
    build_sizer_payload,
    build_validator_payload,
    build_watcher_payload,
)
from polaris.core.pipeline.agents import (
    adaptive_exit_gate,
    position_monitor_gate,
)
from polaris.core.pipeline.gate_orchestrator import log_gate_event
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GATE_POSITION_MONITOR,
    GATE_UNIVERSE_SCANNER,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.core.sizing.constants import production_default_equity_usd
from polaris.scripts._production_indicators import compute_unrealized_pnl_r
from polaris.strategies import BaseStrategy, RawSignal

if TYPE_CHECKING:
    from polaris.scripts.production_paper_loop import ProdLoopState

logger = logging.getLogger(__name__)

CFD_LEVERAGE_DEFAULT = 30.0


def _read_universe_state(
    conn: sqlite3.Connection, *, venue: str, symbol: str, now_ts: int,
) -> tuple[float, float]:
    """Return (spread_bps, listing_age_hours) from the universe row.

    Falls back to (5.0 bps, 365×24 h) when the row is missing — the smoke
    seed path may not have populated universe yet for cold-start runs.
    """
    row = conn.execute(
        "SELECT spread_bps, listing_ts FROM universe "
        "WHERE venue = ? AND symbol = ?",
        (venue, symbol),
    ).fetchone()
    if row is None:
        return (5.0, 24 * 365.0)
    spread_bps = float(row[0] or 5.0)
    listing_ts = int(row[1]) if row[1] is not None else (now_ts - 24 * 3600 * 365)
    listing_age_hours = max(0.0, (now_ts - listing_ts) / 3600.0)
    return (spread_bps, listing_age_hours)


def _strategy_recent_reject(
    conn: sqlite3.Connection, *, strategy_id: str, now_ts: int,
    window_sec: int = 6 * 3600,
) -> bool:
    """True if the strategy logged a reject/halt within the last 6h."""
    cutoff = now_ts - window_sec
    row = conn.execute(
        "SELECT 1 FROM strategy_fault_events "
        "WHERE strategy_id = ? AND fault_type IN ('reject', 'idempotency_conflict') "
        "AND event_ts >= ? LIMIT 1",
        (strategy_id, cutoff),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# G1 → G8 production pipeline
# ---------------------------------------------------------------------------


async def run_pipeline_for_signal(
    *,
    conn: sqlite3.Connection,
    haiku: Any,
    state: ProdLoopState,
    strategy: BaseStrategy,
    sig: RawSignal,
    venue: str,
    symbol: str,
    asset_class: str,
    underlying_group_id: str,
    regime: str,
    bars_atr_pct: float,
    last_price: float,
    universe_rows: list[dict[str, Any]],
    now_ts: int,
    reserve_and_submit: Any,
    phase: str = "P0",
    real_roundtrip: bool = False,
    capital_session: Any = None,
    okx_adapter: Any = None,
) -> None:
    """Run G1 → G2 → G3 → G4 → G5 → G6 → G7 for one validated signal.

    ``reserve_and_submit`` is the AllocatorFence-aware submit closure from
    ``production_paper_loop`` (kept as an injected dep so this module stays
    free of state.fields-binding logic).

    Day 8 spec D: ``start_gate=GATE_UNIVERSE_SCANNER`` so G1/G2 also run.
    Day 8 spec E: G6/G7 use real ``unrealized_pnl_r``; G8 fires on close.
    """
    instrument_id = f"{venue}:{symbol}"
    track: Any = "A" if venue == "okx" else "B"
    leverage = 1.0 if venue == "okx" else CFD_LEVERAGE_DEFAULT
    equity_usd = production_default_equity_usd()

    # Day 8 codex P1 fix: read spread/listing/recent-reject from real state
    # (universe + bars + strategy_fault_events) instead of hard-coded fixtures.
    spread_bps_real, listing_age_h = _read_universe_state(
        conn, venue=venue, symbol=symbol, now_ts=now_ts,
    )
    recent_reject = _strategy_recent_reject(
        conn, strategy_id=sig.strategy_id, now_ts=now_ts,
    )

    g3_payload = build_validator_payload(
        raw_signal=sig, venue=venue, symbol=symbol, instrument_id=instrument_id,
        regime=regime, conn=conn,
    )
    g4_payload = build_watcher_payload(
        spread_bps=spread_bps_real, baseline_p50_spread_bps=spread_bps_real,
        listing_age_hours=listing_age_h, recent_reject_in_6h=recent_reject,
        session_open_shock_window=False, tick_window=[],
    )
    g5_payload = build_sizer_payload(
        raw_signal=sig, venue=venue, symbol=symbol,
        instrument_id=instrument_id, underlying_group_id=underlying_group_id,
        asset_class=asset_class, regime=regime, track=track,
        listing_age_hours=listing_age_h, leverage=leverage,
        equity_usd=equity_usd, conn=conn,
    )
    payload: dict[str, Any] = {
        "signal_id": sig.signal_id,
        "universe": universe_rows,
        "cell_summary": "",
        "raw_signal": g3_payload["raw_signal"],
        **g3_payload, **g4_payload, **g5_payload,
    }
    ctx = GateContext(
        run_id=uuid.uuid4().hex, signal_id=sig.signal_id, position_id=None,
        gate_id=GATE_UNIVERSE_SCANNER, venue=venue, symbol=symbol,
        strategy_id=strategy.metadata.strategy_id, payload=payload,
        started_ts=now_ts, state=SignalLifecycle.RAW,
    )
    orch = GateOrchestrator(conn=conn, haiku_client=haiku, phase=phase)
    results = await orch.run(ctx, start_gate=GATE_UNIVERSE_SCANNER)
    state.pipeline_runs += len(results)
    if any(r.decision == GateDecision.KILL for r in results):
        state.pipeline_kills += 1
    state.g1_runs += 1
    state.g2_emits += 1
    sized_payload: dict[str, Any] | None = None
    for r in results:
        if r.decision == GateDecision.SIZED:
            sized_payload = r.payload.get("sized")
            state.sized_count += 1
            break
    if sized_payload is None:
        return

    notional_usd = max(
        10.0, min(float(sized_payload.get("final_notional_usd", 50.0)), 5_000.0)
    )
    trade = await reserve_and_submit(
        conn=conn, state=state, sig=sig, venue=venue, symbol=symbol,
        asset_class=asset_class, underlying_group_id=underlying_group_id,
        notional_usd=notional_usd, last_price=last_price, now_ts=now_ts,
        real_roundtrip=real_roundtrip, capital_session=capital_session,
        okx_adapter=okx_adapter,
    )
    if trade is None:
        return
    state.open_trades.append(trade)

    # G6 / G7 with real R-multiples.
    pnl_r = compute_unrealized_pnl_r(
        side=sig.side, entry_price=last_price, last_price=last_price,
        atr_pct=max(bars_atr_pct, 1e-4),
    )
    g6_payload = build_monitor_payload(
        position={
            "venue": venue, "symbol": symbol, "side": sig.side,
            "strategy": sig.strategy_id,
            "correlation_group": sig.correlation_group,
            "entry_price": last_price,
            "qty": notional_usd / max(last_price, 1e-6),
        },
        unrealized_pnl_r=pnl_r, max_loss_r=1.0,
    )
    g7_payload = build_exit_payload(
        side=sig.side,
        current_stop_price=last_price * (0.99 if sig.side == "long" else 1.01),
        proposed_stop_price=last_price * (0.985 if sig.side == "long" else 1.015),
        entry_price=last_price, unrealized_pnl_r=pnl_r, max_loss_r=1.0,
        overrides_used=0, seconds_since_last_override=60,
    )
    # Day 8 codex R2 P2 fix: G6/G7/G8 telemetry must use the persisted
    # ``positions.position_id`` so gate_events.position_id joins back to
    # positions for audits + downstream replay.
    persisted_position_id = trade.position_id or f"pos_{sig.signal_id[:10]}"
    g6_ctx = GateContext(
        run_id=ctx.run_id, signal_id=sig.signal_id,
        position_id=persisted_position_id,
        gate_id=GATE_POSITION_MONITOR,
        venue=venue, symbol=symbol, strategy_id=sig.strategy_id,
        payload=g6_payload, started_ts=now_ts, state=SignalLifecycle.SIZED,
    )
    # Day 9 F1 wire: forward GPT client at P1 so G6 fires the gpt_p1 branch
    # (entry-time invocation also exercises the LLM path; F2 live recalc
    # then re-invokes G6 per dirty trigger).
    g6_client = haiku if phase == "P1" else None
    g6_result = await position_monitor_gate(g6_ctx, client=g6_client)
    # Day 8 codex R3 P2 fix: persist G6 result to gate_events so audits can
    # join gate_events.position_id back to positions for the full lifecycle.
    log_gate_event(conn, g6_ctx, g6_result)
    g7_ctx = GateContext(
        run_id=ctx.run_id, signal_id=sig.signal_id,
        position_id=g6_ctx.position_id,
        gate_id=GATE_ADAPTIVE_EXIT,
        venue=venue, symbol=symbol, strategy_id=sig.strategy_id,
        payload={
            **g6_payload, **g7_payload,
            "current_stop_price": g7_payload["current_stop_price"],
        },
        started_ts=now_ts,
        state=(
            SignalLifecycle.MONITORED
            if g6_result.decision == GateDecision.HOLD
            else SignalLifecycle.ACTIVE
        ),
    )
    g7_client = haiku if phase == "P1" else None
    g7_result = await adaptive_exit_gate(g7_ctx, client=g7_client)
    log_gate_event(conn, g7_ctx, g7_result)


