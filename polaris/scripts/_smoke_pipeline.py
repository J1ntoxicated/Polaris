"""Smoke paper-loop gate-pipeline helpers (G3 validate, full G3→G7 chain, close).

Split out of ``smoke_paper_loop`` to keep each module ≤500 LOC. These functions
drive the AI gate pipeline against production-shaped payloads and propagate
closed trades to Layer 4 (cell matrix) + Layer 5 (learners). The tick body in
``smoke_paper_loop`` imports + calls them; shared state lives in ``_smoke_state``.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from polaris.core.cell_matrix import (
    CellContext,
    CellKeyP0,
    TradeClose,
    update_on_trade_close,
)
from polaris.core.data.fills_persist import persist_fill
from polaris.core.learners import ClosedTrade, LearnerScheduler
from polaris.core.pipeline import (
    GateOrchestrator,
    build_exit_payload,
    build_monitor_payload,
    build_sizer_payload,
    build_validator_payload,
    build_watcher_payload,
)
from polaris.core.pipeline.agents.signal_validator import signal_validator_gate
from polaris.core.pipeline.gate_state import (
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    SignalLifecycle,
)
from polaris.scripts._smoke_fills import simulate_close, simulate_open_fill
from polaris.scripts._smoke_gpt_stub import StubGPTClient
from polaris.scripts._smoke_state import FocusEntry, LoopState
from polaris.strategies import BarView, BaseStrategy, RawSignal

logger = logging.getLogger(__name__)


async def _validate_signal(
    strategy: BaseStrategy,
    entry: FocusEntry,
    sig: RawSignal,
    haiku: StubGPTClient,
    state: LoopState,
) -> bool:
    """Run G3 (signal validator) on one signal. Return True on PASS."""
    ctx = GateContext(
        run_id=uuid.uuid4().hex,
        signal_id=sig.signal_id,
        position_id=None,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue=entry.venue,
        symbol=entry.symbol,
        strategy_id=strategy.metadata.strategy_id,
        payload={
            "signal_id": sig.signal_id,
            "side": sig.side,
            "strength": sig.strength,
            "thesis_tag": sig.thesis_tag,
            "raw_signal": {
                "strategy_id": sig.strategy_id,
                "symbol": sig.symbol,
                "side": sig.side,
                "strength": sig.strength,
                "sizing_hint": sig.sizing_hint,
                "ttl_bars": sig.ttl_bars,
                "thesis_tag": sig.thesis_tag,
            },
        },
        started_ts=int(time.time()),
        state=SignalLifecycle.RAW,
    )
    result = await signal_validator_gate(ctx, client=haiku)
    state.pipeline_runs += 1
    if result.decision == GateDecision.KILL:
        state.pipeline_kills += 1
        return False
    return True


async def _run_full_pipeline(
    *,
    strategy: BaseStrategy,
    entry: FocusEntry,
    sig: RawSignal,
    bars: list[BarView],
    conn: Any,
    haiku: StubGPTClient,
    state: LoopState,
) -> None:
    """G3 -> G4 -> G5 -> G6 -> G7 chain with production-shaped payloads.

    Day 6 plumbing:
    - G3 input: build_validator_payload (raw signal + cell + baseline + recent).
    - G4 input: build_watcher_payload (validated signal stamped by G3).
    - G5 input: build_sizer_payload (SignalIntent + StrategyRiskState +
      PortfolioState).
    - G6 input: build_monitor_payload (position + R-multiples).
    - G7 input: build_exit_payload (widen proposal).

    Each gate's output payload is stamped into ``ctx.payload`` so the next
    gate sees it (the orchestrator handles propagation).
    """
    instrument_id = f"{entry.venue}:{entry.symbol}"
    asset_class = entry.asset_class
    regime = "bull_trend"
    track: Any = "A"
    last_price = bars[-1].close if bars else 100.0

    # Compose G3 payload first; subsequent payloads layer on top.
    g3_payload = build_validator_payload(
        raw_signal=sig,
        venue=entry.venue,
        symbol=entry.symbol,
        instrument_id=instrument_id,
        regime=regime,
        conn=conn,
    )
    g4_payload = build_watcher_payload(
        spread_bps=2.0,
        baseline_p50_spread_bps=2.5,
        listing_age_hours=24 * 365.0,  # mature symbol
        recent_reject_in_6h=False,
        session_open_shock_window=False,
        tick_window=[],
    )
    g5_payload = build_sizer_payload(
        raw_signal=sig,
        venue=entry.venue,
        symbol=entry.symbol,
        instrument_id=instrument_id,
        underlying_group_id=entry.symbol.split("-")[0],
        asset_class=asset_class,
        regime=regime,
        track=track,
        listing_age_hours=24 * 365.0,
        leverage=1.0 if entry.venue == "okx" else 30.0,
        equity_usd=10_000.0,
        conn=conn,
    )

    payload: dict[str, Any] = {
        "signal_id": sig.signal_id,
        **g3_payload,
        **g4_payload,
        **g5_payload,
    }
    ctx = GateContext(
        run_id=uuid.uuid4().hex,
        signal_id=sig.signal_id,
        position_id=None,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue=entry.venue,
        symbol=entry.symbol,
        strategy_id=strategy.metadata.strategy_id,
        payload=payload,
        started_ts=int(time.time()),
        state=SignalLifecycle.RAW,
    )
    orch = GateOrchestrator(conn=conn, haiku_client=haiku)
    results = await orch.run(ctx, start_gate=GATE_SIGNAL_VALIDATOR)
    state.full_pipeline_runs += 1
    decisions: dict[int, str] = {}
    for r in results:
        # Use the last seen ctx.gate_id as a fallback; results don't carry id.
        # Each result already advanced the lifecycle, so we count by decision.
        decisions[len(decisions) + 3] = str(r.decision)
    # Track which gates passed for visibility.
    state.pipeline_runs += len(results)
    if any(r.decision == GateDecision.KILL for r in results):
        state.pipeline_kills += 1

    # Did sizing succeed? G5 emits SIZED when final_risk_pct > 0.
    sized_payload: dict[str, Any] | None = None
    for r in results:
        if r.decision == GateDecision.SIZED:
            sized_payload = r.payload.get("sized")
            state.full_pipeline_sized += 1
            break
    if sized_payload is None:
        return

    # Translate the sizing decision into a Fill via simulate_open_fill.
    # The notional is bounded by single_trade_cap × equity in build_sizer_payload.
    notional = float(sized_payload.get("final_notional_usd", 50.0))
    notional = max(10.0, min(notional, 5_000.0))
    fill, trade = simulate_open_fill(
        signal=sig, venue=entry.venue, last_price=last_price, notional_usd=notional
    )
    state.fills_open.append(fill)
    state.open_trades.append(trade)
    try:
        persist_fill(conn, fill, is_close=False)
        state.fills_persisted += 1
    except Exception as exc:  # noqa: BLE001 — must not abort smoke
        logger.error("[full-pipeline] persist_fill failed: %r", exc)

    # G6 / G7: simulate a 1-tick monitor + exit decision so the lifecycle
    # advances. Use a position dict matching G6 schema.
    position = {
        "venue": entry.venue,
        "symbol": entry.symbol,
        "side": sig.side,
        "strategy": sig.strategy_id,
        "correlation_group": entry.symbol.split("-")[0],
        "entry_price": last_price,
        "qty": notional / max(last_price, 1e-6),
    }
    g6_payload = build_monitor_payload(
        position=position,
        unrealized_pnl_r=0.2,  # mid-trade — neither stop nor widen
        max_loss_r=1.0,
    )
    g7_payload = build_exit_payload(
        side=sig.side,
        current_stop_price=last_price * (0.99 if sig.side == "long" else 1.01),
        proposed_stop_price=last_price * (0.985 if sig.side == "long" else 1.015),
        entry_price=last_price,
        unrealized_pnl_r=0.2,
        max_loss_r=1.0,
        overrides_used=0,
        seconds_since_last_override=60,
    )
    # Drive G6 + G7 directly so we exercise the full chain without re-running G3.
    from polaris.core.pipeline.agents import (
        adaptive_exit_gate,
        position_monitor_gate,
    )
    from polaris.core.pipeline.gate_state import (
        GATE_ADAPTIVE_EXIT,
        GATE_POSITION_MONITOR,
    )

    g6_ctx = GateContext(
        run_id=ctx.run_id,
        signal_id=sig.signal_id,
        position_id=f"pos_{sig.signal_id[:10]}",
        gate_id=GATE_POSITION_MONITOR,
        venue=entry.venue,
        symbol=entry.symbol,
        strategy_id=sig.strategy_id,
        payload=g6_payload,
        started_ts=int(time.time()),
        state=SignalLifecycle.SIZED,
    )
    g6_result = await position_monitor_gate(g6_ctx)
    state.gate_pass_counts[6] = state.gate_pass_counts.get(6, 0) + 1
    g7_ctx = GateContext(
        run_id=ctx.run_id,
        signal_id=sig.signal_id,
        position_id=g6_ctx.position_id,
        gate_id=GATE_ADAPTIVE_EXIT,
        venue=entry.venue,
        symbol=entry.symbol,
        strategy_id=sig.strategy_id,
        payload={**g6_payload, **g7_payload, "current_stop_price": g7_payload["current_stop_price"]},
        started_ts=int(time.time()),
        state=(
            SignalLifecycle.MONITORED
            if g6_result.decision == GateDecision.HOLD
            else SignalLifecycle.ACTIVE
        ),
    )
    await adaptive_exit_gate(g7_ctx)
    state.gate_pass_counts[7] = state.gate_pass_counts.get(7, 0) + 1


def _close_oldest_trade(
    state: LoopState, *, conn: Any, tick_idx: int
) -> None:
    """Force-close the oldest open trade and propagate to L4 + L5."""
    trade = state.open_trades.pop(0)
    # Aggressive bias: assume positive PnL so learners + cell_matrix get a win.
    signed_drift = 1.005 if trade.side == "long" else 0.995
    close_fill = simulate_close(trade, exit_price=trade.entry_price * signed_drift)
    trade.closed = True
    trade.pnl_r = 1.0
    state.fills_close.append(close_fill)
    state.closed_trades.append(trade)
    now_ts = int(time.time())
    # Day 6: persist the close-side Fill row with PnL.
    try:
        # Approximate per-trade USD PnL from R-units × notional × ATR proxy.
        pnl_usd = trade.pnl_r * trade.notional_usd * 0.005
        persist_fill(conn, close_fill, is_close=True, pnl_usd=pnl_usd)
        state.fills_persisted += 1
    except Exception as exc:  # noqa: BLE001
        logger.error("[tick %d] persist_fill close failed: %r", tick_idx, exc)
    try:
        update_on_trade_close(
            conn,
            trade=TradeClose(
                key=CellKeyP0(
                    exchange=trade.venue,
                    strategy=trade.strategy_id,
                    ticker=trade.symbol,
                    regime="bull_trend",
                ),
                context=CellContext(
                    group="spot" if trade.venue == "okx" else "cfd",
                    session="asia",
                    direction=trade.side,
                    liquidity_tier="top",
                ),
                pnl_r=trade.pnl_r,
                won=trade.pnl_r > 0.0,
                closed_ts=now_ts,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[tick %d] cell matrix update failed: %r", tick_idx, exc)
    sched = LearnerScheduler(conn, expected_holding_bars=20)
    closed = ClosedTrade(
        trade_id=trade.signal_id,
        strategy_id=trade.strategy_id,
        ticker=trade.symbol,
        venue=trade.venue,
        regime="bull_trend",
        session="asia",
        pnl_r=trade.pnl_r,
        won=trade.pnl_r > 0.0,
        holding_bars=20,
        closed_ts=now_ts,
    )
    for learner in sched.learners:
        learner.update(closed, now_ts=now_ts)



# ---------------------------------------------------------------------------
# Vault append
# ---------------------------------------------------------------------------


def _append_vault(state: LoopState, *, real_okx: dict[str, Any], real_capital: dict[str, Any]) -> Path:
    today = time.strftime("%Y-%m-%d")
    daily_dir = Path("vault/40_ops/daily")
    daily_dir.mkdir(parents=True, exist_ok=True)
    p = daily_dir / f"{today}.md"
    header_needed = not p.exists()
    block = [
        "",
        f"## Day 5 paper loop smoke — {time.strftime('%H:%M:%S')}",
        "",
        f"- signals_emitted: {state.signals_emitted}",
        f"- pipeline_runs: {state.pipeline_runs}",
        f"- pipeline_kills: {state.pipeline_kills}",
        f"- fills_open: {len(state.fills_open)}",
        f"- fills_close: {len(state.fills_close)}",
        f"- closed_trades: {len(state.closed_trades)}",
        f"- real_okx: {real_okx}",
        f"- real_capital: {real_capital}",
    ]
    text = ("\n".join(block) + "\n")
    if header_needed:
        text = (
            "---\n"
            f"date: {today}\n"
            "type: daily-ops\n"
            "tags: [polaris, daily, p0-sprint]\n"
            "---\n\n# Polaris daily ops log\n"
        ) + text
    with p.open("a", encoding="utf-8") as fh:
        fh.write(text)
    return p
