"""Gate 6 — Position Monitor (deterministic Python).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G6 + Q4 protective fail-open + Q8 swap)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Position Monitor)
- .claude/plans/ai_conductor_architecture_2026-05-30.md (P3 — per-position GPT branch removed)

Outputs: HOLD / ADJUST_EXIT / EXIT_NOW / SWAP_STRATEGY.

Decision contract (deterministic, no LLM):
- EXIT_NOW on stop hit (``pnl_r <= -max_loss_r``) — hard loss rail.
- SWAP_STRATEGY when a candidate matches Q8 (correlation_group/side/venue/symbol).
- ADJUST_EXIT in the winner-widen window (``pnl_r > +0.7R``).
- HOLD otherwise.

ai_conductor P3 (2026-05-30): the per-position GPT (P1) branch was removed. In
live telemetry G6 GPT returned HOLD 99.97% (3434 HOLD / 1 EXIT_NOW) — the model
never materially moved the decision, while the deterministic hard stop + Q8 swap
fast-path already owned every real action. Precise *signal* exits remain owned by
the G7 FSM (``evaluate_exit``), which runs downstream. This gate is now pure
Python: cost removed, behaviour preserved. The ``client`` / ``model`` /
``call_cache`` / ``tick_idx`` parameters are retained for caller compatibility
but are inert (no network call is ever made).

Aggressive bias (Jin 2026-05-07): DEMO/PAPER, virtual capital. The deterministic
rules never KILL a position defensively — only the hard loss rail exits, and
winners stay open (HOLD / ADJUST_EXIT widen).

Fail-open (Q4): missing position → HOLD (never KILL the position).
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GateContext,
    GateDecision,
    GateResult,
)

__all__ = [
    "DEFAULT_MAX_LOSS_R",
    "G6_DECISION_ENUM",
    "WIDEN_WINDOW_R",
    "evaluate_strategy_swap",
    "position_monitor_gate",
]

logger = logging.getLogger(__name__)

WIDEN_WINDOW_R: float = 0.7
DEFAULT_MAX_LOSS_R: float = 1.0
G6_DECISION_ENUM: list[str] = ["HOLD", "ADJUST_EXIT", "EXIT_NOW", "SWAP_STRATEGY"]


def evaluate_strategy_swap(
    *,
    position: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    """Q8 — only allow swap when candidate matches correlation_group, side, venue, symbol."""
    if position.get("correlation_group") != candidate.get("correlation_group"):
        return False
    if position.get("side") != candidate.get("side"):
        return False
    if position.get("venue") != candidate.get("venue"):
        return False
    return position.get("symbol") == candidate.get("symbol")


def _python_decision(
    *,
    pos: dict[str, Any],
    pnl_r: float,
    max_loss_r: float,
    candidate: dict[str, Any] | None,
) -> GateResult:
    """Deterministic decision rules (the sole G6 decision path)."""
    if pnl_r <= -abs(max_loss_r):
        return GateResult(
            decision=GateDecision.EXIT_NOW,
            next_gate=GATE_ADAPTIVE_EXIT,
            payload={"reason": "stop_hit", "pnl_r": pnl_r},
            model_used="python",
        )
    if isinstance(candidate, dict) and evaluate_strategy_swap(position=pos, candidate=candidate):
        return GateResult(
            decision=GateDecision.SWAP_STRATEGY,
            next_gate=GATE_ADAPTIVE_EXIT,
            payload={"swap": candidate, "from_strategy": pos.get("strategy")},
            model_used="python",
        )
    if pnl_r > WIDEN_WINDOW_R:
        return GateResult(
            decision=GateDecision.ADJUST_EXIT,
            next_gate=GATE_ADAPTIVE_EXIT,
            payload={"reason": "widen_window", "pnl_r": pnl_r},
            model_used="python",
        )
    return GateResult(
        decision=GateDecision.HOLD,
        next_gate=GATE_ADAPTIVE_EXIT,
        payload={"pnl_r": pnl_r},
        model_used="python",
    )


async def position_monitor_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    model: str | None = None,
    call_cache: Any | None = None,
    tick_idx: int = 0,
) -> GateResult:
    """Gate 6 dispatcher — deterministic Python (no LLM).

    Inputs from ``ctx.payload``:
        ``position`` (dict, required), ``unrealized_pnl_r`` (float),
        ``max_loss_r`` (float), ``swap_candidate`` (dict | None).

    The ``client`` / ``model`` / ``call_cache`` / ``tick_idx`` parameters are
    retained for caller compatibility (ai_conductor P3 removed the GPT branch)
    but are inert — no network call is ever made.

    Fail-open (Q4): missing position → HOLD (never KILL).
    """
    pos = ctx.payload.get("position", {})
    if not isinstance(pos, dict) or not pos:
        return GateResult(
            decision=GateDecision.HOLD,
            next_gate=GATE_ADAPTIVE_EXIT,
            payload={"reason": "missing_position"},
            model_used="python",
        )
    pnl_r = float(ctx.payload.get("unrealized_pnl_r", 0.0))
    max_loss_r = float(ctx.payload.get("max_loss_r", DEFAULT_MAX_LOSS_R))
    candidate = ctx.payload.get("swap_candidate")
    candidate_dict: dict[str, Any] | None = (
        candidate if isinstance(candidate, dict) else None
    )
    return _python_decision(
        pos=pos, pnl_r=pnl_r, max_loss_r=max_loss_r, candidate=candidate_dict,
    )
