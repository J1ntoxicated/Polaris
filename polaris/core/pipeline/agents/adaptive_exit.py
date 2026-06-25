"""Gate 7 — Adaptive Exit (Python P0, GPT-5.5 P1).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q9 floor-only widening)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Adaptive Exit)

Phase contract:
- **P0** (``client is None``): deterministic widening rules (default ATR
  exit = floor; override only widens, never tightens; max 5/trade, 30s
  cooldown, never below max-loss / BEP). ``model_used="python"``.
- **P1** (``client is not None``): GPT decides among
  HOLD / WIDEN / TIGHTEN / EXIT_NOW. **TIGHTEN is rejected** when the
  proposed stop is *closer* than the default ATR floor (Q9 conservative
  trap avoidance). WIDEN must push the stop *farther* than the current
  level. EXIT_NOW falls through to a normal exit.

Aggressive bias (Jin 2026-05-07): TIGHTEN suggestions that violate the
default ATR floor are reversed to HOLD — the LLM cannot dampen winners
with a smaller stop. WIDEN is the only direction the floor permits.

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

from polaris.core.pipeline.agents._gpt_client import (
    DEFAULT_TIMEOUT_SEC,
    GPT_P0_MODEL,
    GPT_P1_MODEL,
    call_gpt,
    make_system_prefix,
)
from polaris.core.pipeline.agents._shadow_rules import ShadowDecision
from polaris.core.pipeline.agents.shadow_log import log_shadow_event
from polaris.core.pipeline.config import ai_free_mode
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
    "G7_MAX_TOKENS",
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
G7_MAX_TOKENS: int = 200
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


def _format_exit_context(exit_context: dict[str, Any]) -> str:
    """Render the #26 precise-exit context block as readable prompt lines.

    DISPLAY-ONLY: this enriches what G7 *sees* (regime, ATR trajectory,
    MFE/MAE excursion, FSM exit_state, holding time, recent price action) so it
    can make a time/momentum/regime-aware HOLD/WIDEN/TIGHTEN/EXIT_NOW call
    instead of rubber-stamping HOLD. It does NOT alter any decision rail.
    """
    lines: list[str] = []
    if "regime" in exit_context:
        lines.append(f"- regime: {exit_context['regime']}")
    if "exit_state" in exit_context:
        lines.append(f"- exit_state (FSM phase): {exit_context['exit_state']}")
    if "held_seconds" in exit_context:
        lines.append(f"- held_seconds: {exit_context['held_seconds']}")
    if "mfe_r" in exit_context or "mae_r" in exit_context:
        mfe = exit_context.get("mfe_r")
        mae = exit_context.get("mae_r")
        lines.append(f"- MFE/MAE (R): MFE={mfe} MAE={mae}")
    if "atr_pct" in exit_context or "atr_slope" in exit_context:
        lines.append(
            f"- ATR: atr_pct={exit_context.get('atr_pct')} "
            f"atr_slope={exit_context.get('atr_slope')} "
            "(slope>0 = expanding volatility)"
        )
    if "volume_z" in exit_context:
        lines.append(f"- volume_z: {exit_context['volume_z']}")
    if "peak_price" in exit_context:
        lines.append(f"- peak_price: {exit_context['peak_price']}")
    if "recent_close_first" in exit_context:
        lines.append(
            f"- recent price action: {exit_context['recent_close_first']} -> "
            f"{exit_context['recent_close_last']} "
            f"over last {exit_context.get('recent_close_n')} bars"
        )
    if not lines:
        return ""
    return "# Market / position context\n" + "\n".join(lines) + "\n\n"


def _build_g7_user_prompt(
    proposal: dict[str, Any],
    exit_context: dict[str, Any] | None = None,
) -> str:
    """Compact prompt for G7 GPT call.

    When ``exit_context`` is supplied (#26), regime / ATR trajectory /
    MFE-MAE / FSM exit_state / holding time / recent price action are surfaced
    before the proposal so G7 reasons precisely. The widening rails text is
    unchanged — enrichment is context-only, not a new decision branch.
    """
    context_block = _format_exit_context(exit_context or {})
    return (
        "# Open position — adaptive exit\n"
        f"{proposal}\n\n"
        f"{context_block}"
        "Decide HOLD / WIDEN / TIGHTEN / EXIT_NOW.\n"
        "WIDEN = push stop FARTHER from price (winners run further).\n"
        "TIGHTEN = pull stop CLOSER (rejected if it crosses default ATR floor).\n"
        "Use the context: a stalled winner in a fading regime may EXIT_NOW; a "
        "strong winner with expanding favourable momentum should WIDEN. "
        "Default = HOLD when unsure. Aggressive bias = winner extension > "
        "premature lock-in.\n"
        'Output JSON: {"decision":"HOLD|WIDEN|TIGHTEN|EXIT_NOW",'
        '"new_exit_atr":2.5,"reason":"..."}'
    )


def _coerce_g7_decision(text: str) -> str | None:
    s = text.strip().upper()
    if s in {"HOLD", "WIDEN", "TIGHTEN", "EXIT_NOW"}:
        return s
    return None


async def adaptive_exit_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    model: str = GPT_P1_MODEL,
    shadow_conn: sqlite3.Connection | None = None,
    ai_free: bool | None = None,
) -> GateResult:
    """Gate 7 dispatcher (Python P0 + GPT P1 + W3 AI-free).

    ``ai_free`` (W3 cutover — ``POLARIS_AI_FREE``, default ON; ``None`` reads
    the env): the deterministic Q9 widening rail IS the live decision even
    when a ``client`` is supplied — ``_p1_decide`` never fires (zero GPT
    calls, ``model_used="python"``) and no divergence shadow row is written
    (GPT absent → nothing to compare). flag=0 → legacy P0/P1 split below,
    byte-identical.

    Reads ``ctx.payload`` for the widening proposal; default = HOLD with the
    current stop (floor preserved). Fail-open: errors → HOLD.

    G8 (Reflector) only runs after the trade actually closes. If the upstream
    state is ``EXIT_PENDING`` (G6 emitted EXIT_NOW or venue close in flight),
    we chain to G8; otherwise terminate the per-tick loop here so the
    lifecycle stays coherent (no premature reflection of an open position).

    P1 GPT branch (``client is not None``, see :func:`_p1_decide`):
    - HOLD / WIDEN / TIGHTEN / EXIT_NOW.
    - WIDEN must satisfy Q9 widening rail (further than current,
      above max-loss floor); else reverts to HOLD.
    - TIGHTEN that crosses the default ATR floor is reversed to HOLD
      (conservative trap avoidance — Jin aggressive bias mandate).

    ``shadow_conn`` (G7 divergence SHADOW — instrumentation, behavior 0):
    when supplied AND the GPT branch ran, the deterministic Q9 rail decision
    for the SAME proposal is logged vs the GPT decision (+ the RAW GPT label)
    into ``gate_shadow_events`` — the G7-rubber-stamp / P0-demotion evidence.
    The returned decision is byte-identical with or without it. P0
    (``client is None``) skips the shadow entirely: the rail IS the live
    decision there, so a row would be guaranteed mismatch=0 noise.
    """
    state = getattr(ctx, "state", None)
    next_gate_after_exit: int | None = (
        GATE_POST_TRADE_REFLECTOR
        if state and state.value in {"EXIT_PENDING", "CLOSED"}
        else None
    )
    # Probe TIGHTEN consumer (deterministic, P0+P1): a ratchet-safe tighter trail fed
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
    use_ai_free = ai_free if ai_free is not None else ai_free_mode()
    if client is None or use_ai_free:
        # P0 / W3 AI-FREE: the deterministic Q9 widening rail is the live
        # decision (``model_used="python"``); the GPT branch + its shadow
        # never fire.
        return _python_widen_only(
            proposal=proposal, next_gate_after_exit=next_gate_after_exit,
        )
    result, gpt_raw = await _p1_decide(
        ctx, proposal=proposal, client=client, model=model,
        next_gate_after_exit=next_gate_after_exit,
    )
    _log_g7_shadow(
        shadow_conn, ctx=ctx, proposal=proposal, result=result,
        gpt_raw=gpt_raw, next_gate_after_exit=next_gate_after_exit,
    )
    return result


def _log_g7_shadow(
    shadow_conn: sqlite3.Connection | None,
    *,
    ctx: GateContext,
    proposal: dict[str, Any],
    result: GateResult,
    gpt_raw: str,
    next_gate_after_exit: int | None,
) -> None:
    """Append the rails-vs-GPT G7 divergence row (instrumentation only).

    ``technical`` = the pure Q9 widening rail re-run on the SAME proposal
    (HOLD | ADJUST_EXIT); ``gpt_decision`` = what the live P1 branch actually
    returned. ``technical_flags`` carries ``site:<caller>`` (live_recalc /
    orchestrator — sample-bias separation in analysis) and ``gpt_raw:<label>``
    (the RAW GPT verdict incl. rail-reversed WIDEN/TIGHTEN and ERR fallbacks —
    the rubber-stamp HOLD-rate numerator). Fail-open: never crashes G7; no-op
    when ``shadow_conn`` is None (gated call byte-identical).
    """
    if shadow_conn is None:
        return
    try:
        rails = _python_widen_only(
            proposal=proposal, next_gate_after_exit=next_gate_after_exit,
        )
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
                decision=rails.decision,
                reason=str(rails.payload.get("reason", "")),
                flags=(f"site:{site}", f"gpt_raw:{gpt_raw}"),
            ),
            gpt_decision=result.decision,
            cell_warm=False,
        )
    except Exception as exc:  # noqa: BLE001 — instrumentation must never crash G7
        logger.warning(
            "[g7-shadow] row dropped %s:%s: %r", ctx.venue, ctx.symbol, exc,
        )


async def _p1_decide(
    ctx: GateContext,
    *,
    proposal: dict[str, Any],
    client: Any,
    model: str,
    next_gate_after_exit: int | None,
) -> tuple[GateResult, str]:
    """P1 GPT branch (pure move out of ``adaptive_exit_gate`` — behavior 0).

    Returns ``(result, gpt_raw)`` where ``gpt_raw`` is the RAW GPT verdict
    (``HOLD``/``WIDEN``/``TIGHTEN``/``EXIT_NOW``) BEFORE any rail reversal, or
    ``ERR`` on the GPT-error / unparseable-decision fallbacks — reasons are
    free text so the raw label cannot be recovered downstream (shadow B4).
    """
    p1_root = GPT_P1_MODEL.lower()
    p0_root = GPT_P0_MODEL.lower()
    m = model.lower()
    if m == p1_root or m.startswith(f"{p1_root}-"):
        model_label = "gpt_p1"
    elif m == p0_root or m.startswith(f"{p0_root}-"):
        model_label = "gpt"
    else:
        model_label = "gpt"

    system = make_system_prefix(
        role=(
            "Polaris Adaptive Exit — winner-extension bias. WIDEN = push "
            "stop further from entry to let winners run. TIGHTEN that "
            "crosses the default ATR floor will be rejected upstream as a "
            "conservative trap. Default = HOLD (current stop preserved)."
        ),
        decision_enum=G7_DECISION_ENUM,
        cell_summary="",
        baseline_summary="",
        recent_trades_summary="",
    )
    exit_context = ctx.payload.get("exit_context")
    res = await call_gpt(
        client=client,
        system_prefix=system,
        user_prompt=_build_g7_user_prompt(
            proposal,
            exit_context if isinstance(exit_context, dict) else None,
        ),
        max_tokens=G7_MAX_TOKENS,
        model=model,
        timeout_sec=DEFAULT_TIMEOUT_SEC,
    )
    if res.error or res.parsed is None:
        fallback = _python_widen_only(
            proposal=proposal, next_gate_after_exit=next_gate_after_exit,
        )
        return GateResult(
            decision=fallback.decision,
            next_gate=fallback.next_gate,
            payload={**fallback.payload, "fallback": "gpt_error", "error": res.error},
            model_used=model_label,
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            error=res.error,
        ), "ERR"
    parsed = res.parsed
    decision_text = _coerce_g7_decision(str(parsed.get("decision", "HOLD")))
    if decision_text is None:
        fallback = _python_widen_only(
            proposal=proposal, next_gate_after_exit=next_gate_after_exit,
        )
        return GateResult(
            decision=fallback.decision,
            next_gate=fallback.next_gate,
            payload={**fallback.payload, "fallback": "bad_decision",
                     "raw": str(parsed.get("decision", ""))[:50]},
            model_used=model_label,
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
        ), "ERR"
    side = str(proposal.get("side", "long"))
    current_stop = float(proposal.get("current_stop_price", 0.0))
    proposed_stop = float(proposal.get("proposed_stop_price", current_stop))
    reason_g7 = str(parsed.get("reason", ""))[:200]
    new_exit_atr = parsed.get("new_exit_atr")

    if decision_text == "EXIT_NOW":
        return GateResult(
            decision=GateDecision.EXIT_NOW,
            next_gate=GATE_POST_TRADE_REFLECTOR,
            payload={
                "reason": reason_g7 or "gpt_exit_now",
                "stop_price": current_stop,
                "decision_source": model_label,
            },
            model_used=model_label,
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
        ), "EXIT_NOW"

    if decision_text == "WIDEN":
        # WIDEN must push stop FARTHER than current; verify Q9 rails too.
        if not _is_widening(side, current_stop=current_stop, proposed_stop=proposed_stop):
            # Reverse to HOLD — stop wasn't actually widened in the proposal.
            return GateResult(
                decision=GateDecision.HOLD,
                next_gate=next_gate_after_exit,
                payload={
                    "stop_price": current_stop,
                    "widening_applied": False,
                    "reason": "widen_not_farther",
                    "decision_source": model_label,
                },
                model_used=model_label,
                latency_ms=res.latency_ms,
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
            ), "WIDEN"
        # Re-use Q9 deterministic check on the proposal so all hard rails fire.
        rail = _python_widen_only(
            proposal=proposal, next_gate_after_exit=next_gate_after_exit,
        )
        rail.payload["decision_source"] = model_label
        rail.payload["reason"] = (
            "gpt_widen_accepted" if rail.payload.get("widening_applied") else
            f"gpt_widen_blocked:{rail.payload.get('reason', '')}"
        )
        if isinstance(new_exit_atr, (int, float)):
            rail.payload["new_exit_atr"] = float(new_exit_atr)
        return GateResult(
            decision=rail.decision,
            next_gate=rail.next_gate,
            payload=rail.payload,
            model_used=model_label,
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
        ), "WIDEN"

    if decision_text == "TIGHTEN":
        # Conservative trap avoidance: TIGHTEN that brings stop CLOSER than
        # the proposal's current_stop is reversed to HOLD. We never tighten
        # past the default ATR floor — the proposal's current_stop IS the
        # default floor at this layer.
        # (Long: tighter = stop > current. Short: tighter = stop < current.)
        s_lc = side.lower()
        is_tightening = (
            (s_lc == "long" and proposed_stop > current_stop)
            or (s_lc == "short" and proposed_stop < current_stop)
        )
        if is_tightening:
            return GateResult(
                decision=GateDecision.HOLD,
                next_gate=next_gate_after_exit,
                payload={
                    "stop_price": current_stop,
                    "widening_applied": False,
                    "reason": "tighten_rejected_floor",
                    "decision_source": model_label,
                },
                model_used=model_label,
                latency_ms=res.latency_ms,
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
            ), "TIGHTEN"
        # Not actually a tighten — fall through as HOLD.
        return GateResult(
            decision=GateDecision.HOLD,
            next_gate=next_gate_after_exit,
            payload={
                "stop_price": current_stop,
                "widening_applied": False,
                "reason": "tighten_not_closer",
                "decision_source": model_label,
            },
            model_used=model_label,
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
        ), "TIGHTEN"

    # HOLD (default).
    return GateResult(
        decision=GateDecision.HOLD,
        next_gate=next_gate_after_exit,
        payload={
            "stop_price": current_stop,
            "widening_applied": False,
            "reason": reason_g7 or "gpt_hold",
            "decision_source": model_label,
        },
        model_used=model_label,
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
    ), "HOLD"
