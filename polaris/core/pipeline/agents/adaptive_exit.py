"""Gate 7 — Adaptive Exit (deterministic Q9 widening rail).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q9 floor-only widening)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Adaptive Exit)

Contract: the deterministic widening rules ARE the live decision (default
ATR exit = floor; override only widens, never tightens; max 5/trade, 30s
cooldown, never below max-loss / BEP). ``model_used="python"``.

P2a group B (2026-07-16, gate-pipeline value audit): the legacy P1 GPT
decision path (HOLD/WIDEN/TIGHTEN/EXIT_NOW via GPT) is removed outright — it
was measured DORMANT in production (``POLARIS_AI_FREE`` default ON since
2026-06-11 zeroed in-loop G3/G4/G7 GPT calls) and is deleted rather than kept
as a switchable-but-unreachable branch. ``client``/``model``/``ai_free``
remain on the signature ONLY for call-site compatibility (orchestrator +
existing tests) — none are read; the Q9 rail is unconditionally the
decision. The #32 AI exit-timing judge (``_maybe_judge_exit`` below) is a
SEPARATE, out-of-scope call path — left byte-identical.

Aggressive bias (Jin 2026-05-07): only the WIDEN direction is permitted past
the default ATR floor — TIGHTEN is a probe-consumer-only path
(``_python_tighten``), never a GPT suggestion now.

Hard rails (Q9):
- never exceed ``max_loss_r`` hard cap.
- ``unrealized_pnl_r > +0.7R`` required to widen.
- never widen back below BEP once already above BEP.
- max 5 overrides per trade, 30s cooldown.

Fail-open (Q4): on any error → HOLD with current stop (floor preserved).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from polaris.core.pipeline.agents._shadow_rules import ShadowDecision
from polaris.core.pipeline.agents.ai_judge import (
    apply_exit_verdict,
    judge_exit,
    log_judge_event,
)
from polaris.core.pipeline.agents.judge_gate import (
    evidence_robustness,
    robust_min,
)
from polaris.core.pipeline.agents.shadow_log import log_shadow_event
from polaris.core.pipeline.config import ai_judge_mode
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GATE_POST_TRADE_REFLECTOR,
    GateContext,
    GateDecision,
    GateResult,
)

__all__ = [
    "COOLDOWN_SEC",
    "G7_DECISION_ENUM",
    "MAX_OVERRIDES_PER_TRADE",
    "WIDEN_WINDOW_R",
    "adaptive_exit_gate",
    "can_tighten_exit",
    "can_widen_exit",
]

logger = logging.getLogger(__name__)

WIDEN_WINDOW_R: float = 0.7
MAX_OVERRIDES_PER_TRADE: int = 5
COOLDOWN_SEC: int = 30
# Kept as the canonical decision-enum reference (the deterministic rail only
# ever emits HOLD/ADJUST_EXIT, but this enum documents the full decision
# space the gate historically covered — still consulted by name in tests).
G7_DECISION_ENUM: list[str] = ["HOLD", "WIDEN", "TIGHTEN", "EXIT_NOW"]


def can_widen_exit(
    *,
    side: str,
    current_stop_price: float,
    proposed_stop_price: float,
    entry_price: float,
    unrealized_pnl_r: float,
    max_loss_r: float,
    overrides_used: int,
    seconds_since_last_override: int,
    initial_stop_price: float | None = None,
) -> tuple[bool, str]:
    """Q9 floor-only widening check. Returns (allowed, reason).

    Hard rails (Q9):
    - max overrides per trade.
    - 30s cooldown after last override.
    - unrealized_pnl_r > 0.7 to widen.
    - long: new_stop < current_stop (more loss tolerance, further from entry).
    - short: new_stop > current_stop.
    - never widen below max_loss_r floor (uses ``initial_stop_price`` when
      provided; otherwise treats max_loss_r as a fraction of entry price).
    - never widen back below BEP once already above BEP.
    """
    if overrides_used >= MAX_OVERRIDES_PER_TRADE:
        return False, "override_cap"
    if seconds_since_last_override < COOLDOWN_SEC:
        return False, "cooldown"
    if unrealized_pnl_r <= WIDEN_WINDOW_R:
        return False, "below_widen_window"
    side_lc = side.lower()

    # Compute the absolute max-loss floor: prefer caller-provided initial_stop,
    # otherwise approximate from max_loss_r fraction of entry price.
    if side_lc == "long":
        if proposed_stop_price >= current_stop_price:
            return False, "not_widening_long"
        floor = (
            float(initial_stop_price)
            if initial_stop_price is not None
            else entry_price * (1.0 - max(0.0, max_loss_r) * 0.05)
        )
        # Long stops are loss when below entry; the absolute floor is the lower
        # bound — proposed must not go lower than the floor.
        if proposed_stop_price < floor:
            return False, "below_max_loss"
        if proposed_stop_price < entry_price and current_stop_price >= entry_price:
            return False, "below_bep"
    elif side_lc == "short":
        if proposed_stop_price <= current_stop_price:
            return False, "not_widening_short"
        floor = (
            float(initial_stop_price)
            if initial_stop_price is not None
            else entry_price * (1.0 + max(0.0, max_loss_r) * 0.05)
        )
        if proposed_stop_price > floor:
            return False, "above_max_loss"
        if proposed_stop_price > entry_price and current_stop_price <= entry_price:
            return False, "above_bep"
    else:
        return False, "bad_side"
    return True, "ok"


def _python_widen_only(
    *,
    proposal: dict[str, Any],
    next_gate_after_exit: int | None,
) -> GateResult:
    """Run the deterministic Q9 widening check (used at P0 + as P1 fallback)."""
    initial_stop = proposal.get("initial_stop_price")
    initial_stop_f: float | None = float(initial_stop) if initial_stop is not None else None
    allowed, reason = can_widen_exit(
        side=str(proposal.get("side", "long")),
        current_stop_price=float(proposal.get("current_stop_price", 0.0)),
        proposed_stop_price=float(proposal.get("proposed_stop_price", 0.0)),
        entry_price=float(proposal.get("entry_price", 0.0)),
        unrealized_pnl_r=float(proposal.get("unrealized_pnl_r", 0.0)),
        max_loss_r=float(proposal.get("max_loss_r", 1.0)),
        overrides_used=int(proposal.get("overrides_used", 0)),
        seconds_since_last_override=int(
            proposal.get("seconds_since_last_override", COOLDOWN_SEC + 1)
        ),
        initial_stop_price=initial_stop_f,
    )
    new_stop = (
        float(proposal["proposed_stop_price"]) if allowed else float(proposal["current_stop_price"])
    )
    return GateResult(
        decision=GateDecision.ADJUST_EXIT if allowed else GateDecision.HOLD,
        next_gate=next_gate_after_exit,
        payload={
            "stop_price": new_stop,
            "widening_applied": allowed,
            "reason": reason,
        },
        model_used="python",
    )


def _is_widening(side: str, *, current_stop: float, proposed_stop: float) -> bool:
    """Long: proposed < current (further below entry). Short: proposed > current."""
    s = side.lower()
    if s == "long":
        return proposed_stop < current_stop
    if s == "short":
        return proposed_stop > current_stop
    return False


def can_tighten_exit(
    *,
    side: str,
    current_stop_price: float,
    proposed_stop_price: float,
    overrides_used: int,
    seconds_since_last_override: int,
) -> tuple[bool, str]:
    """Ratchet-safe TIGHTEN check (probe TIGHTEN consumer). Returns (allowed, reason).

    The MIRROR of ``can_widen_exit`` for the opposite direction — used when a probe
    reads adverse and the consumer feeds a TIGHTER trail to G7 (precise exit TIMING,
    [[g7_tick_trail_atr_scale_2026-06-25]] sibling). A tighten pulls the stop CLOSER
    to price (long: stop UP toward the peak; short: stop DOWN). This is flow_not_block:
    it only adjusts the trail — never a HOLD->EXIT_NOW block, never a size cut, never
    an entry veto. The G6 -1.0R hard rail / BEP / size / entry side are all untouched.

    Hard rails:
    - max overrides per trade (reuses ``MAX_OVERRIDES_PER_TRADE``).
    - ``COOLDOWN_SEC`` debounce after the last override (same-position re-fire gap).
    - long: proposed_stop > current_stop (closer to price). short: proposed < current.
      A 'tighten' that would LOOSEN the stop (move it away from price) is rejected so
      the ratchet (never loosen an already-set stop) is preserved — exactly the
      inverse rail of ``can_widen_exit``'s ``not_widening_*``.

    No unrealized-PnL window gate (unlike widen's +0.7R): tightening toward profit is
    ALWAYS safe to allow — the precise-exit value is highest on the adverse positions
    that never reach the widen window. The ratchet below still forbids loosening.
    """
    if overrides_used >= MAX_OVERRIDES_PER_TRADE:
        return False, "override_cap"
    if seconds_since_last_override < COOLDOWN_SEC:
        return False, "cooldown"
    side_lc = side.lower()
    if side_lc == "long":
        # Tighter long stop is HIGHER (closer to price). Equal-or-lower = no tighten.
        if proposed_stop_price <= current_stop_price:
            return False, "not_tightening_long"
    elif side_lc == "short":
        # Tighter short stop is LOWER (closer to price). Equal-or-higher = no tighten.
        if proposed_stop_price >= current_stop_price:
            return False, "not_tightening_short"
    else:
        return False, "bad_side"
    return True, "ok"


def _python_tighten(
    *,
    proposal: dict[str, Any],
    next_gate_after_exit: int | None,
) -> GateResult:
    """Deterministic ratchet-safe tighten (probe TIGHTEN consumer path).

    ADJUST_EXIT with the tighter stop when ``can_tighten_exit`` allows; HOLD with the
    current stop otherwise. Mirrors ``_python_widen_only`` (same GateResult shape) so
    the recalc caller persists ``stop_price`` identically — the next precise-exit tick
    ratchets toward that tighter stop (and the ratchet still forbids loosening it).
    """
    allowed, reason = can_tighten_exit(
        side=str(proposal.get("side", "long")),
        current_stop_price=float(proposal.get("current_stop_price", 0.0)),
        proposed_stop_price=float(proposal.get("proposed_stop_price", 0.0)),
        overrides_used=int(proposal.get("overrides_used", 0)),
        seconds_since_last_override=int(
            proposal.get("seconds_since_last_override", COOLDOWN_SEC + 1)
        ),
    )
    new_stop = (
        float(proposal["proposed_stop_price"])
        if allowed
        else float(proposal["current_stop_price"])
    )
    return GateResult(
        decision=GateDecision.ADJUST_EXIT if allowed else GateDecision.HOLD,
        next_gate=next_gate_after_exit,
        payload={
            "stop_price": new_stop,
            "tightening_applied": allowed,
            "reason": f"probe_tighten:{reason}",
        },
        model_used="python",
    )


async def _maybe_judge_exit(
    ctx: GateContext,
    *,
    det_result: GateResult,
    proposal: dict[str, Any],
    judge_client: Any | None,
    shadow_conn: sqlite3.Connection | None,
) -> GateResult:
    """Run the #32 AI exit-TIMING judge over the deterministic G7 result (non-blocking).

    No-op when ``judge_client`` is None (deterministic result returned byte-identical).
    The judge reads the bot's own info + exit_context and judges PROTECT / EXTEND /
    TIGHTEN_ON_CONFIRMED_DECAY — TIMING only. Its verdict type has NO EXIT_NOW / KILL
    member, so a deterministic HOLD / ADJUST_EXIT can never become a forced cut. The
    Q9 widen / can_tighten rails + the -1.0R rail stay deterministic-owned; active
    mode annotates which timing direction the judge preferred, shadow logs only.

    A+B CALL GATE (#32 axes — THE flooding hotspot). G7+judge fires per-position
    per-recalc-tick, so the judge is consulted only on a DECISION MOMENT. The recalc
    caller (which owns the per-position cooldown dict + the pre-computed mode / rung /
    big-move signals) stamps ``payload['judge_exit_escalate']`` — True only when A's
    exit predicate fired AND the cooldown elapsed / rung advanced. We AND that with B
    (evidence robustness). Absent key (orchestrator entry-time path / tests) → default
    True so legacy callers are unchanged. ``escalate=False`` NEVER blocks — the
    deterministic HOLD / ADJUST_EXIT (Q9 rail) flows unchanged (flow_not_block).
    """
    if judge_client is None:
        return det_result
    escalate_a = ctx.payload.get("judge_exit_escalate", True)
    rob = evidence_robustness(ctx.payload, now_ts=int(ctx.started_ts))
    if not (bool(escalate_a) and rob.score >= robust_min()):
        return det_result
    outcome = await judge_exit(
        ctx,
        deterministic=det_result.decision,
        subject=proposal,
        client=judge_client,
    )
    mode = ai_judge_mode()
    log_judge_event(
        shadow_conn, ctx=ctx, gate_id=GATE_ADAPTIVE_EXIT, outcome=outcome, mode=mode
    )
    return apply_exit_verdict(outcome, deterministic_result=det_result, mode=mode)


def _farther_stop(side: str, *, stop: float, atr_step: float) -> float:
    """Push a stop one ATR-step FARTHER from price (long: down; short: up)."""
    return stop - atr_step if side.lower() == "long" else stop + atr_step


def _closer_stop(side: str, *, stop: float, atr_step: float) -> float:
    """Pull a stop one ATR-step CLOSER to price (long: up; short: down)."""
    return stop + atr_step if side.lower() == "long" else stop - atr_step


def _apply_exit_request(
    judged: GateResult,
    *,
    proposal: dict[str, Any],
    next_gate_after_exit: int | None,
) -> GateResult:
    """#32 axis-C — route an active-mode EXTEND / TIGHTEN judge REQUEST through the rail.

    Reads the request keys the active-mode ``apply_exit_verdict`` stamped onto the
    judged payload. NO request (shadow / PROTECT / judge absent / weak evidence) →
    ``judged`` returned UNCHANGED (byte-identical). When present, the rail's
    (allowed, reason) is FINAL:
    - EXTEND → push proposed_stop one extra ATR farther + re-run ``can_widen_exit``
      (max_loss / -1.0R floor, pnl_r>0.7R, override cap, cooldown). Reject → HOLD,
      stop unchanged (the verdict enum has no EXIT_NOW — a cut is unrepresentable).
    - TIGHTEN → synth a stop one ATR-step closer + route ``can_tighten_exit`` (never
      loosen — ratchet preserved). Reject → HOLD, stop unchanged.
    flow_not_block: widen only moves the stop farther, tighten only ratchets it
    closer; neither forces an exit or cuts size, and the -1.0R rail is never bypassed.
    """
    payload = judged.payload
    widen_req = payload.get("ai_judge_widen_request")
    tighten_req = payload.get("ai_judge_tighten_request")
    if not isinstance(widen_req, dict) and not isinstance(tighten_req, dict):
        return judged
    side = str(proposal.get("side", "long"))
    entry_price = float(proposal.get("entry_price", 0.0))
    current_stop = float(proposal.get("current_stop_price", 0.0))
    # ATR-step in PRICE units: re-derive from the proposal's own widen span (the
    # deterministic ATR distance current→proposed) so the judge step shares the
    # rail's ruler; fall back to a 1% entry slice if the span is degenerate.
    proposed_stop = float(proposal.get("proposed_stop_price", current_stop))
    atr_one = abs(current_stop - proposed_stop) or max(abs(entry_price) * 0.01, 1e-6)

    if isinstance(widen_req, dict):
        extra = float(widen_req.get("extend_atr", 1.0))
        # Start from the deterministic proposed_stop (already one ATR farther) and
        # push EXTRA*atr farther, then re-run the Q9 rail on that proposal.
        farther = _farther_stop(side, stop=proposed_stop, atr_step=extra * atr_one)
        widen_proposal = {**proposal, "proposed_stop_price": farther}
        rail = _python_widen_only(
            proposal=widen_proposal, next_gate_after_exit=next_gate_after_exit,
        )
        rail.payload["ai_judge_request"] = "EXTEND"
        return GateResult(
            decision=rail.decision,
            next_gate=rail.next_gate,
            payload={**payload, **rail.payload},
            model_used=judged.model_used,
            latency_ms=judged.latency_ms,
            error=judged.error,
            skipped=judged.skipped,
            input_tokens=judged.input_tokens,
            output_tokens=judged.output_tokens,
        )

    # TIGHTEN request.
    assert isinstance(tighten_req, dict)
    step = float(tighten_req.get("tighten_atr", 0.5))
    closer = _closer_stop(side, stop=current_stop, atr_step=step * atr_one)
    tighten_proposal = {
        "side": side,
        "current_stop_price": current_stop,
        "proposed_stop_price": closer,
        "overrides_used": int(proposal.get("overrides_used", 0)),
        "seconds_since_last_override": int(
            proposal.get("seconds_since_last_override", COOLDOWN_SEC + 1)
        ),
    }
    rail = _python_tighten(
        proposal=tighten_proposal, next_gate_after_exit=next_gate_after_exit,
    )
    rail.payload["ai_judge_request"] = "TIGHTEN"
    return GateResult(
        decision=rail.decision,
        next_gate=rail.next_gate,
        payload={**payload, **rail.payload},
        model_used=judged.model_used,
        latency_ms=judged.latency_ms,
        error=judged.error,
        skipped=judged.skipped,
        input_tokens=judged.input_tokens,
        output_tokens=judged.output_tokens,
    )


async def adaptive_exit_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    model: str = "",
    shadow_conn: sqlite3.Connection | None = None,
    ai_free: bool | None = None,
    judge_client: Any | None = None,
) -> GateResult:
    """Gate 7 dispatcher — deterministic Q9 widening rail (P2a group B).

    Reads ``ctx.payload`` for the widening proposal; default = HOLD with the
    current stop (floor preserved). Fail-open: errors → HOLD.

    ``client`` / ``model`` / ``ai_free`` are accepted ONLY for call-site
    compatibility (the orchestrator + existing tests still pass them) — none
    are read. GPT is never called from this gate.

    G8 (Reflector) only runs after the trade actually closes. If the upstream
    state is ``EXIT_PENDING`` (G6 emitted EXIT_NOW or venue close in flight),
    we chain to G8; otherwise terminate the per-tick loop here so the
    lifecycle stays coherent (no premature reflection of an open position).

    ``shadow_conn`` (measurement continuity): when supplied, the Q9 rail
    decision is logged to ``gate_shadow_events``. ``gpt_decision`` is always
    ``None`` now (no GPT call to compare against — not a comparison gap, the
    call itself is gone).
    """
    state = getattr(ctx, "state", None)
    next_gate_after_exit: int | None = (
        GATE_POST_TRADE_REFLECTOR
        if state and state.value in {"EXIT_PENDING", "CLOSED"}
        else None
    )
    # Probe TIGHTEN consumer (deterministic): a ratchet-safe tighter trail fed
    # by G6 when a probe reads adverse ([[g7_tick_trail_atr_scale_2026-06-25]] sibling).
    # flow_not_block — precise exit TIMING only (ADJUST_EXIT-tighten / HOLD), never a
    # block or size cut; the -1.0R rail / entry / size are untouched. When absent the
    # widen path below is byte-identical (no tighten_proposal → no behaviour change).
    tighten_proposal = ctx.payload.get("tighten_proposal")
    if isinstance(tighten_proposal, dict):
        return _python_tighten(
            proposal=tighten_proposal, next_gate_after_exit=next_gate_after_exit,
        )
    proposal = ctx.payload.get("widen_proposal")
    if not isinstance(proposal, dict):
        return GateResult(
            decision=GateDecision.HOLD,
            next_gate=next_gate_after_exit,
            payload={"stop_price": ctx.payload.get("current_stop_price")},
            model_used="python",
        )
    det_result = _python_widen_only(
        proposal=proposal, next_gate_after_exit=next_gate_after_exit,
    )
    _log_g7_shadow(
        shadow_conn, ctx=ctx, technical=det_result,
        next_gate_after_exit=next_gate_after_exit,
    )
    # #32 AI JUDGE (exit-timing): a per-ticker, STRUCTURALLY non-blocking
    # exit-timing judgment over the bot's own info + exit_context. No EXIT_NOW
    # / KILL path exists in its verdict type, so the deterministic HOLD /
    # ADJUST_EXIT (Q9 rail) can never become a forced cut. No-op when
    # ``judge_client`` is None (byte-identical to the rail-only path).
    judged = await _maybe_judge_exit(
        ctx, det_result=det_result, proposal=proposal,
        judge_client=judge_client, shadow_conn=shadow_conn,
    )
    # #32 axis-C EXIT behaviour: an active-mode EXTEND / TIGHTEN verdict stamped a
    # REQUEST onto the judged payload. Route it THROUGH the deterministic rail
    # (can_widen_exit / can_tighten_exit) — never around it. The rail's
    # (allowed, reason) is FINAL (max_loss/-1.0R floor, pnl window, ratchet,
    # override cap, cooldown). Reject → stop UNCHANGED (flow_not_block; the verdict
    # enum has no EXIT_NOW so a forced cut is unrepresentable).
    return _apply_exit_request(
        judged, proposal=proposal, next_gate_after_exit=next_gate_after_exit,
    )


def _log_g7_shadow(
    shadow_conn: sqlite3.Connection | None,
    *,
    ctx: GateContext,
    technical: GateResult,
    next_gate_after_exit: int | None,
) -> None:
    """Log the Q9 rail's technical decision for measurement continuity.

    P2a group B: ``gpt_decision`` is always ``None`` now (the legacy P1 GPT
    branch this row used to compare against is gone — see module docstring).
    ``technical_flags`` still carries ``site:<caller>`` (live_recalc /
    orchestrator — sample-bias separation in analysis). Fail-open: never
    crashes G7; no-op when ``shadow_conn`` is None.
    """
    if shadow_conn is None:
        return
    try:
        regime = ctx.payload.get("regime")
        if not isinstance(regime, str) or not regime:
            market_view = ctx.payload.get("market_view")
            regime = (
                str(market_view.get("regime", ""))
                if isinstance(market_view, dict)
                else ""
            )
        site = str(ctx.payload.get("g7_shadow_site", "") or "orchestrator")
        log_shadow_event(
            shadow_conn,
            run_id=ctx.run_id,
            signal_id=ctx.signal_id,
            gate_id=GATE_ADAPTIVE_EXIT,
            venue=ctx.venue,
            symbol=ctx.symbol,
            regime=regime,
            technical=ShadowDecision(
                decision=technical.decision,
                reason=str(technical.payload.get("reason", "")),
                flags=(f"site:{site}",),
            ),
            gpt_decision=None,
            cell_warm=False,
        )
    except Exception as exc:  # noqa: BLE001 — instrumentation must never crash G7
        logger.warning(
            "[g7-shadow] row dropped %s:%s: %r", ctx.venue, ctx.symbol, exc,
        )
