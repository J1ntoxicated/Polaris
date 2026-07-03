"""Gate 5 — Entry Sizer (Python P0, Sonnet P1).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G5)
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (T4)

P0: dispatch the validated signal through ``polaris.core.sizing.compute_size``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

from polaris.core.pipeline.agents.judge_gate import size_up_boost
from polaris.core.pipeline.gate_state import (
    GATE_POSITION_MONITOR,
    GateContext,
    GateDecision,
    GateResult,
)
from polaris.core.sizing import (
    PortfolioState,
    SignalIntent,
    StrategyRiskState,
    compute_size,
)
from polaris.core.sizing.probe_notional import resolve_strategy_class

__all__ = ["entry_sizer_gate"]


async def entry_sizer_gate(
    ctx: GateContext,
    *,
    conn: sqlite3.Connection,
) -> GateResult:
    """Gate 5 dispatcher (Python deterministic T4).

    Inputs from ``ctx.payload``:
        ``signal_intent`` (SignalIntent | dict),
        ``risk_state`` (StrategyRiskState),
        ``portfolio`` (PortfolioState).

    Fail-closed: any error → KILL (entry-side gate, Q4).
    """
    intent_raw = ctx.payload.get("signal_intent")
    risk_state = ctx.payload.get("risk_state")
    portfolio = ctx.payload.get("portfolio")
    if not isinstance(intent_raw, SignalIntent):
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "missing_signal_intent"},
            model_used="python",
        )
    if not isinstance(risk_state, StrategyRiskState) or not isinstance(portfolio, PortfolioState):
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "missing_risk_or_portfolio"},
            model_used="python",
        )

    # #32 axis-C SIZE_UP: the A+B-gated entry judge (G3/G4) stamps
    # ``ai_judge_size_up_intent`` into the result payload (merged into ctx.payload by
    # the orchestrator) ONLY in active mode + robust evidence. Thread its conviction
    # boost into the SAME single continuous scalar via ``judge_conviction`` — NOT a
    # post-hoc multiply, NOT a new T4 chain element (the fold + re-clamp happen inside
    # compute_size, mirroring regime_scalar; the 9-stack count is unchanged). Absent /
    # shadow / False → conviction 1.0 → byte-identical sizing (flow_not_block: never a
    # cut). The downstream tier_amp / cell_mult / single-trade cap / headroom_min() all
    # still bind and clip after, so SIZE_UP can never breach the absolute ceiling.
    if ctx.payload.get("ai_judge_size_up_intent") and intent_raw.judge_conviction == 1.0:
        intent_raw = replace(intent_raw, judge_conviction=size_up_boost())

    # pts-classes (group D): thread the live EARN/PROVE/BENCH class for this
    # (venue, strategy) — a missing row (bootstrap hasn't run yet) resolves to
    # EARN, byte-identical to pre-pts-classes sizing.
    strategy_class = resolve_strategy_class(
        conn, venue=intent_raw.venue, strategy_id=intent_raw.strategy
    )
    intent_raw = replace(intent_raw, strategy_class=strategy_class)

    try:
        sized = compute_size(
            conn,
            intent=intent_raw,
            risk_state=risk_state,
            portfolio=portfolio,
            now_ts=int(ctx.started_ts),
        )
    except (ValueError, sqlite3.Error) as exc:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "sizing_error", "error": repr(exc)},
            model_used="python",
        )

    if sized.final_risk_pct <= 0.0:
        # Capital rotation (Jin 2026-05-30): surface the conviction-derived
        # ``proposed_risk_pct`` (the capital SCALE the sizer asked for BEFORE a
        # binding cap clipped final_risk_pct to 0) so the rotation trigger seam
        # can register this capital-blocked signal as a rotation candidate.
        # Display/telemetry only on the KILL — it does NOT re-open the entry and
        # adds NO T4 multiplier (9-stack ban intact).
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={
                "reason": "sizing_zero",
                "binding_cap": sized.binding_cap,
                "proposed_risk_pct": sized.proposed.proposed_risk_pct,
            },
            model_used="python",
        )

    payload: dict[str, Any] = {
        "sized": {
            "final_risk_pct": sized.final_risk_pct,
            "final_notional_usd": sized.final_notional_usd,
            "leverage": sized.leverage,
            "binding_cap": sized.binding_cap,
            "proposal": {
                "base_risk_pct": sized.proposed.base_risk_pct,
                "continuous_scalar": sized.proposed.continuous_scalar,
                "tier_amplifier": sized.proposed.tier_amplifier,
                "cell_routing_mult": sized.proposed.cell_routing_mult,
                "listing_watchdog_mult": sized.proposed.listing_watchdog_mult,
                "proposed_risk_pct": sized.proposed.proposed_risk_pct,
            },
        }
    }
    return GateResult(
        decision=GateDecision.SIZED,
        next_gate=GATE_POSITION_MONITOR,
        payload=payload,
        model_used="python",
    )
