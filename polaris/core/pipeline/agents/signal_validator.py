"""Gate 3 — Signal Validator (deterministic, fail-closed on missing input).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G3 + Q4 fail-closed + Q6 prompt)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Signal Validator)

Behavior:
- Input: raw_signal + cell_matrix routing summary.
- Output: PASS / MODIFY (the technical rule raises no entry-block KILL —
  ``flow_not_block``; only ``missing_raw_signal`` fail-closes). MODIFY carries
  ``strength_scalar in [0.5, 1.5]``.

P2a group B (2026-07-16, gate-pipeline value audit): the legacy in-loop GPT
decision path is removed outright — it was measured DORMANT in production
(``POLARIS_AI_FREE`` default ON since 2026-06-11 zeroed in-loop G3/G4/G7 GPT
calls) and is deleted rather than kept as a switchable-but-unreachable branch.
``client``/``ai_free`` remain on the signature ONLY for call-site
compatibility (orchestrator + existing tests) — they are never read; the
technical rule is unconditionally the decision. The #32 AI entry judge
(``_maybe_judge_entry`` below) is a SEPARATE, out-of-scope call path — left
byte-identical.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Final

from polaris.core.pipeline.agents._shadow_rules import (
    CELL_WARM_MIN_N_EFF,
    G3ShadowInputs,
    ShadowDecision,
    g3_shadow_inputs_from_payload,
    technical_validate_decision,
)
from polaris.core.pipeline.agents.ai_judge import (
    apply_entry_verdict,
    judge_entry,
    log_judge_event,
)
from polaris.core.pipeline.agents.judge_gate import (
    evidence_robustness,
    robust_min,
    should_escalate_entry,
)
from polaris.core.pipeline.agents.shadow_log import log_shadow_event
from polaris.core.pipeline.config import ai_judge_mode
from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    GateResult,
)

__all__ = [
    "MODIFY_MAX",
    "MODIFY_MIN",
    "signal_validator_gate",
]

logger = logging.getLogger(__name__)

MODIFY_MIN: Final[float] = 0.5
MODIFY_MAX: Final[float] = 1.5


def _g3_technical(ctx: GateContext) -> tuple[G3ShadowInputs, ShadowDecision]:
    """Compute the G3 deterministic technical decision from ``ctx.payload``.

    Shared by the legacy shadow logger AND the AI-free primary path so the two
    can never drift. The technical rule raises NO entry-block KILL — losing is
    never a block (``flow_not_block``); its only outputs are PASS / conservative
    MODIFY.
    """
    inp = g3_shadow_inputs_from_payload(ctx.payload)
    return inp, technical_validate_decision(inp)


def _log_g3_shadow(
    ctx: GateContext,
    shadow_conn: sqlite3.Connection | None,
    gpt_decision: GateDecision | None,
) -> None:
    """Compute the G3 deterministic technical rule + log it vs the GPT decision.

    AI-conductor P0 SHADOW (behavior 0): the technical decision is logged for the
    acceptance gate and NEVER returned. ``cold cell = pass-through`` and
    ``net_edge`` is not consulted (both enforced inside the rule). No-op when
    ``shadow_conn`` is None.
    """
    if shadow_conn is None:
        return
    inp, technical = _g3_technical(ctx)
    regime = str(ctx.payload.get("regime", ""))
    log_shadow_event(
        shadow_conn,
        run_id=ctx.run_id,
        signal_id=ctx.signal_id,
        gate_id=GATE_SIGNAL_VALIDATOR,
        venue=ctx.venue,
        symbol=ctx.symbol,
        regime=regime,
        technical=technical,
        gpt_decision=gpt_decision,
        cell_warm=inp.n_eff >= CELL_WARM_MIN_N_EFF,
    )


async def _maybe_judge_entry(
    ctx: GateContext,
    *,
    det_result: GateResult,
    judge_client: Any | None,
    shadow_conn: sqlite3.Connection | None,
) -> GateResult:
    """Run the #32 AI entry judge over the deterministic G3 result (non-blocking).

    No-op when ``judge_client`` is None (the deterministic result is returned
    byte-identical — judge absent). Otherwise the judge runs over the bot's own
    information, logs a ``gate_shadow_events`` row (measurement + pass-through
    tracking), and — only in ``active`` mode — annotates the result flow-additively.
    The judge can NEVER turn the deterministic PASS/MODIFY into a KILL (its verdict
    type has no block member). Fail-open: any judge failure → deterministic result.

    A+B CALL GATE (#32 axes): the judge is consulted only when the deterministic G3
    decision is uncertain/boundary (A) AND the bot's own evidence is robust (B). A
    clean decisive warm signal with sparse evidence flows deterministically with NO
    GPT call and NO shadow row (anti-flooding). ``escalate=False`` NEVER blocks — the
    deterministic PASS/MODIFY flows unchanged (flow_not_block).
    """
    if judge_client is None:
        return det_result
    validated = det_result.payload.get("validated_signal", {})
    scalar = (
        float(validated.get("strength_scalar", 1.0))
        if isinstance(validated, dict)
        else 1.0
    )
    cell = ctx.payload.get("cell_routing", {})
    n_eff = float(cell.get("n_eff", 0.0) or 0.0) if isinstance(cell, dict) else 0.0
    esc_a, reason_a = should_escalate_entry(
        gate="G3",
        decision=det_result.decision.name,
        scalar=scalar,
        n_eff=n_eff,
        recent_reject_in_6h=bool(ctx.payload.get("recent_reject_in_6h", False)),
    )
    rob = evidence_robustness(ctx.payload, now_ts=int(ctx.started_ts))
    if not (esc_a and rob.score >= robust_min()):
        # A-skip OR B-weak-evidence → deterministic default (no GPT, no shadow row).
        return det_result
    outcome = await judge_entry(
        ctx,
        deterministic=det_result.decision,
        subject=det_result.payload.get("validated_signal", {}),
        client=judge_client,
    )
    mode = ai_judge_mode()
    log_judge_event(
        shadow_conn, ctx=ctx, gate_id=GATE_SIGNAL_VALIDATOR, outcome=outcome, mode=mode
    )
    return apply_entry_verdict(outcome, deterministic_result=det_result, mode=mode)


async def signal_validator_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    shadow_conn: sqlite3.Connection | None = None,
    ai_free: bool | None = None,
    judge_client: Any | None = None,
) -> GateResult:
    """Gate 3 dispatcher — deterministic-only (P2a group B).

    Reads inputs from ``ctx.payload``: ``raw_signal``, ``cell_routing``.
    Fail-closed: missing/empty ``raw_signal`` → KILL. Otherwise the technical
    rule drives — it raises NO entry-block KILL (``flow_not_block`` — losing
    is never a block), so the signal always flows on with PASS / conservative
    MODIFY.

    ``client`` / ``ai_free`` are accepted ONLY for call-site compatibility
    (the orchestrator + existing tests still pass them) — neither is read.
    GPT is never called from this gate.

    ``shadow_conn`` (measurement continuity): when supplied, the technical
    decision is logged to ``gate_shadow_events`` for the acceptance-gate
    dashboards. ``gpt_decision`` is always ``None`` now (no GPT call to
    compare against — not a comparison gap, the call itself is gone).
    """
    raw_signal = ctx.payload.get("raw_signal", {})
    if not isinstance(raw_signal, dict) or not raw_signal:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "missing_raw_signal"},
            model_used="python",
        )

    _inp, technical = _g3_technical(ctx)
    logger.info(
        "[gate/G3-validator] decision=%s scalar=%.2f regime=%s reason=%s sym=%s "
        "(deterministic, GPT=0)",
        technical.decision.name,
        technical.scalar,
        str(ctx.payload.get("regime", "")),
        technical.reason,
        str(raw_signal.get("symbol", raw_signal.get("ticker", "?"))),
    )
    _log_g3_shadow(ctx, shadow_conn, gpt_decision=None)
    det_result = GateResult(
        decision=technical.decision,
        next_gate=GATE_PRE_ENTRY_WATCHER,
        payload={
            "validated_signal": {
                **raw_signal,
                "strength_scalar": technical.scalar,
            },
            "reason": technical.reason,
        },
        model_used="python",
    )
    # #32 AI JUDGE (entry-rationale) — separate, out-of-scope call path, left
    # byte-identical. No-op when ``judge_client`` is None.
    return await _maybe_judge_entry(
        ctx, det_result=det_result, judge_client=judge_client,
        shadow_conn=shadow_conn,
    )
