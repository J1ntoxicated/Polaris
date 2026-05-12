"""Gate 2 — Strategy Signal Generator (Python only, no LLM).

Spec source: vault/30_components/layer-2-per-gate-pipeline.md (Q3 G2 = Python).

P0: pass-through gate that adopts whatever raw_signal the strategy module
already emitted. The 7 strategies have not been ported to ``polaris/strategies/``
yet (Day 4 scope), so this gate just promotes ``ctx.payload['raw_signal']`` to
``validated`` state if present, else KILL.
"""

from __future__ import annotations

from polaris.core.pipeline.gate_state import (
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    GateResult,
)

__all__ = ["strategy_signal_gate"]


async def strategy_signal_gate(ctx: GateContext) -> GateResult:
    """Promote a Python-emitted raw_signal forward.

    Fail-closed at strategy_local level (Q4): if no raw_signal, the signal
    flow ends without affecting other strategies.
    """
    raw = ctx.payload.get("raw_signal")
    if not isinstance(raw, dict):
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "no_raw_signal"},
            model_used="python",
            latency_ms=0,
        )
    return GateResult(
        decision=GateDecision.PASS,
        next_gate=GATE_SIGNAL_VALIDATOR,
        payload={"raw_signal": raw},
        model_used="python",
        latency_ms=0,
    )
