"""Gate 5 — Entry Sizer (Python P0, Sonnet P1).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G5)
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (T4)

P0: dispatch the validated signal through ``polaris.core.sizing.compute_size``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

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
