"""Gate 3 — Signal Validator (deterministic; G3->G5 direct wire, P2a group A).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G3 + Q4 fail-closed + Q6 prompt)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Signal Validator)

Behavior:
- Input: raw_signal + cell_matrix routing summary + tick window / spread /
  listing baseline (formerly G4's own inputs — the orchestrator primes
  ``ctx.payload`` with all of it up front, so it was always available here).
- Output: PASS / MODIFY (the G3 technical rule raises no entry-block KILL —
  ``flow_not_block``); KILL only on ``missing_raw_signal`` or a crossed book
  (microstructure broken — inherited from G4, see below). MODIFY carries
  ``strength_scalar in [0.5, 1.5]``.

P2a group B (2026-07-16, gate-pipeline value audit): the legacy in-loop GPT
decision path is removed outright — it was measured DORMANT in production
(``POLARIS_AI_FREE`` default ON since 2026-06-11 zeroed in-loop G3/G4/G7 GPT
calls) and is deleted rather than kept as a switchable-but-unreachable branch.
``client``/``ai_free`` remain on the signature ONLY for call-site
compatibility (orchestrator + existing tests) — they are never read; the
technical rule is unconditionally the decision.

P2a group A (2026-07-16): Gate 4 (Pre-Entry Watcher) is abolished as a
decision STEP — the audit measured it 1447/1447 PROCEED (100% no-op; its own
GPT call was already removed in group B). G3 now wires directly to G5
(``next_gate=GATE_ENTRY_SIZER``). G4's decision logic (the legacy GPT
dispatch + ``no_gpt_client``/schema-violation KILLs) is gone with the module;
its side-effect content is relocated to ``_g4_frontgate.py`` (fast-path
eligibility, the crossed-book KILL rail + stale/spread/drift ``watch_flags``,
the frontgate VWAP/TTM-squeeze shadow tags, and the #32 AI entry-TIMING
judge) and called from here UNCHANGED so nothing is lost. The #32 AI
entry-RATIONALE judge (``_maybe_judge_entry``, G3's own) is untouched. Both
judge calls are a SEPARATE, out-of-scope subsystem — left byte-identical in
their own logic.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Final

from polaris.core.pipeline.agents._g4_frontgate import (
    fast_path_context_from_payload,
    is_fast_path_eligible,
    log_g4_frontgate_shadow,
    maybe_judge_timing,
)
from polaris.core.pipeline.agents._shadow_rules import (
    CELL_WARM_MIN_N_EFF,
    G3ShadowInputs,
    ShadowDecision,
    g3_shadow_inputs_from_payload,
    g4_shadow_inputs_from_payload,
    technical_validate_decision,
    technical_watch_decision,
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
    GATE_ENTRY_SIZER,
    GATE_SIGNAL_VALIDATOR,
    GateContext,
    GateDecision,
    GateResult,
)

# Relocated symbols re-exported here for call-site / test compatibility (the
# public "G4 fast-path" API used to live in pre_entry_watcher.py).
__all__ = [
    "MODIFY_MAX",
    "MODIFY_MIN",
    "is_fast_path_eligible",
    "signal_validator_gate",
]

logger = logging.getLogger(__name__)

MODIFY_MIN: Final[float] = 0.5
MODIFY_MAX: Final[float] = 1.5


def _g3_technical(ctx: GateContext) -> tuple[G3ShadowInputs, ShadowDecision]:
    """Compute the G3 deterministic technical decision from ``ctx.payload``.

    The technical rule raises NO entry-block KILL — losing is never a block
    (``flow_not_block``); its only outputs are PASS / conservative MODIFY.
    """
    inp = g3_shadow_inputs_from_payload(ctx.payload)
    return inp, technical_validate_decision(inp)


def _log_g3_shadow(
    ctx: GateContext,
    shadow_conn: sqlite3.Connection | None,
    gpt_decision: GateDecision | None,
) -> None:
    """Compute the G3 deterministic technical rule + log it for measurement.

    ``cold cell = pass-through`` and ``net_edge`` is not consulted (both
    enforced inside the rule). No-op when ``shadow_conn`` is None.
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
    """Run the #32 AI entry-RATIONALE judge over the G3 result (non-blocking).

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
    md_conn: sqlite3.Connection | None = None,
    ai_free: bool | None = None,
    judge_client: Any | None = None,
) -> GateResult:
    """Gate 3 dispatcher — deterministic-only, G3->G5 direct wire (P2a).

    Reads inputs from ``ctx.payload``: ``raw_signal``, ``cell_routing``, plus
    G4's former inputs (``tick_window``, ``spread_bps``,
    ``baseline_p50_spread_bps``, ...) — all primed up front by the caller
    before the pipeline starts, so nothing new is required upstream.
    Fail-closed: missing/empty ``raw_signal`` → KILL. Otherwise the technical
    rule drives — it raises NO entry-block KILL (``flow_not_block`` — losing
    is never a block), EXCEPT the crossed-book rail inherited from G4 (broken
    microstructure, not a market judgment call).

    ``client`` / ``ai_free`` are accepted ONLY for call-site compatibility
    (the orchestrator + existing tests still pass them) — neither is read.
    GPT is never called from this gate. ``md_conn`` threads to the frontgate
    VWAP/TTM shadow tagger (marketdata-domain bars read); ``None`` falls back
    to ``shadow_conn``.

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
    validated_signal = {**raw_signal, "strength_scalar": technical.scalar}

    # G4 fast-path (relocated verbatim, P2a group A): an EFFICIENCY skip of
    # the slow per-tick watch computation below — unaffected by MODIFY (whose
    # scalar is always <=1.0 and can never clear the 1.25 strength floor).
    fp = fast_path_context_from_payload(ctx, validated_signal)
    if is_fast_path_eligible(fp, ctx.stream_profile):
        return GateResult(
            decision=technical.decision,
            next_gate=GATE_ENTRY_SIZER,
            payload={
                "validated_signal": validated_signal,
                "watched_signal": validated_signal,
                "reason": technical.reason,
                "fast_path": True,
            },
            model_used="python_fast_path",
            latency_ms=0,
            skipped=True,
        )

    # G4 crossed-book KILL + stale/spread/drift watch_flags (relocated
    # verbatim, P2a group A). KILL only on a crossed book (microstructure
    # broken — the pre-existing deterministic KILL set, no new block);
    # stale (per-ticker baseline) / wide spread / drift stay FLAGS.
    now_ref = ctx.started_ts if ctx.started_ts > 0 else int(time.time())
    g4_inp = g4_shadow_inputs_from_payload(ctx.payload, now_ts=now_ref)
    watch = technical_watch_decision(g4_inp)
    if watch.decision == GateDecision.KILL:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": watch.reason},
            model_used="python",
        )
    det_payload: dict[str, Any] = {
        "validated_signal": validated_signal,
        "watched_signal": validated_signal,
        "reason": technical.reason,
    }
    if watch.flags:
        det_payload["watch_flags"] = list(watch.flags)
    det_result = GateResult(
        decision=technical.decision,
        next_gate=GATE_ENTRY_SIZER,
        payload=det_payload,
        model_used="python",
    )
    # frontgate-scan items #6 (VWAP/AVWAP timing) + #9 (TTM Squeeze) —
    # behavior-0 watch tags, logged only, never influence det_result above.
    log_g4_frontgate_shadow(ctx, shadow_conn, md_conn=md_conn, inp=g4_inp)
    # #32 AI JUDGE — entry-rationale (G3's own) then entry-timing (G4's own,
    # relocated). Both are a SEPARATE, out-of-scope call path, left
    # byte-identical. No-op when ``judge_client`` is None.
    result = await _maybe_judge_entry(
        ctx, det_result=det_result, judge_client=judge_client,
        shadow_conn=shadow_conn,
    )
    return await maybe_judge_timing(
        ctx, det_result=result, judge_client=judge_client,
        shadow_conn=shadow_conn,
    )
