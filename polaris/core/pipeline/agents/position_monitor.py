"""Gate 6 — Position Monitor (Python P0, GPT-5.5 P1).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G6 + Q4 protective fail-open + Q8 swap)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Position Monitor)

Outputs: HOLD / ADJUST_EXIT / EXIT_NOW / SWAP_STRATEGY.

Phase contract:
- **P0** (``client is None``): deterministic Python rules — HOLD by default,
  EXIT_NOW on stop hit (``pnl_r <= -max_loss_r``), ADJUST_EXIT in widen
  window (``pnl_r > +0.7R``), SWAP_STRATEGY when candidate matches Q8.
  ``model_used="python"``.
- **P1** (``client is not None``): the same hard rails fire first (loss
  stop + Q8 swap = python fast-path), then GPT decides HOLD vs.
  ADJUST_EXIT vs. EXIT_NOW vs. SWAP_STRATEGY for the open band.
  ``model_used="gpt_p1"``.

Aggressive bias (Jin 2026-05-07): the LLM prompt explicitly notes DEMO/PAPER
trading on OKX simulated venue + virtual capital — real-money safety
arguments are INVALID. Default = HOLD only when the model emits an
explicit HOLD; ambiguous responses fall through to the Python rules so
winners stay open longer.

Fail-open (Q4): on any error → Python rules (never KILL the position).
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.core.pipeline.agents._gpt_client import (
    DEFAULT_TIMEOUT_SEC,
    GPT_P0_MODEL,
    GPT_P1_MODEL,
    call_gpt,
    make_system_prefix,
)
from polaris.core.pipeline.g6_call_gate import (
    G6CallCache,
    G6CallContext,
    should_call_gpt,
)
from polaris.core.pipeline.gate_state import (
    GATE_ADAPTIVE_EXIT,
    GateContext,
    GateDecision,
    GateResult,
)

__all__ = [
    "DEFAULT_MAX_LOSS_R",
    "G6_DECISION_ENUM",
    "G6_MAX_TOKENS",
    "WIDEN_WINDOW_R",
    "evaluate_strategy_swap",
    "position_monitor_gate",
]

logger = logging.getLogger(__name__)

WIDEN_WINDOW_R: float = 0.7
DEFAULT_MAX_LOSS_R: float = 1.0
G6_MAX_TOKENS: int = 200
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
    """Deterministic P0 rules — used at P0 and as the P1 fail-open fallback."""
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


def _build_g6_user_prompt(
    *,
    pos: dict[str, Any],
    pnl_r: float,
    max_loss_r: float,
    market_view: dict[str, Any],
    recent_ticks: list[dict[str, Any]],
) -> str:
    """Compact JSON-output prompt for G6 GPT call."""
    side = str(pos.get("side", "long"))
    entry = float(pos.get("entry_price", 0.0))
    last = float(pos.get("last_price", entry))
    held = int(pos.get("held_seconds", 0))
    cell_score = float(pos.get("cell_score", 0.0))
    regime = str(market_view.get("regime", "chop"))
    atr_now = float(market_view.get("atr_pct", 0.0))
    vol_now = float(market_view.get("volume_now", 0.0))
    ticks_summary = recent_ticks[-10:]  # cap prompt size
    return (
        f"# Open position\n"
        f'{{"side":"{side}","entry":{entry},"last":{last},'
        f'"uPnL_R":{pnl_r:.3f},"held_sec":{held},'
        f'"cell_score":{cell_score:.3f},"max_loss_r":{max_loss_r}}}\n'
        f"# Market view\n"
        f'{{"regime":"{regime}","atr_pct":{atr_now},"volume_now":{vol_now}}}\n'
        f"# Recent ticks (last {len(ticks_summary)})\n"
        f"{ticks_summary}\n\n"
        'Output JSON: {"decision":"HOLD|ADJUST_EXIT|EXIT_NOW|SWAP_STRATEGY",'
        '"reason":"...","new_stop_distance":1.5}'
    )


def _coerce_decision(text: str) -> GateDecision | None:
    """Map a GPT decision string to a ``GateDecision`` (case-insensitive)."""
    s = text.strip().upper()
    mapping = {
        "HOLD": GateDecision.HOLD,
        "ADJUST_EXIT": GateDecision.ADJUST_EXIT,
        "EXIT_NOW": GateDecision.EXIT_NOW,
        "SWAP_STRATEGY": GateDecision.SWAP_STRATEGY,
    }
    return mapping.get(s)


def _reuse_decision(decision_str: str, pnl_r: float) -> GateResult:
    """Rebuild a G6 result from a cached prior GPT decision (no call).

    Used by the call-efficiency gate (#15): when the cooldown has not elapsed
    and the decision context is unchanged we reuse the last GPT verdict instead
    of paying for another call. Quality is preserved because any *material*
    context change bypasses this path (``should_call_gpt`` returns True).
    """
    mapping = {
        "HOLD": GateDecision.HOLD,
        "ADJUST_EXIT": GateDecision.ADJUST_EXIT,
        "EXIT_NOW": GateDecision.EXIT_NOW,
        "SWAP_STRATEGY": GateDecision.SWAP_STRATEGY,
    }
    decision = mapping.get(decision_str, GateDecision.HOLD)
    # SWAP_STRATEGY without a live candidate cannot be re-emitted blindly; the
    # Q8 fast-path above already handles eligible swaps, so reuse → HOLD here.
    if decision == GateDecision.SWAP_STRATEGY:
        decision = GateDecision.HOLD
    return GateResult(
        decision=decision,
        next_gate=GATE_ADAPTIVE_EXIT,
        payload={
            "reason": "g6_cached_decision",
            "pnl_r": pnl_r,
            "decision_source": "cache",
        },
        model_used="python_fast_path",
    )


async def position_monitor_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    model: str = GPT_P1_MODEL,
    call_cache: G6CallCache | None = None,
    tick_idx: int = 0,
) -> GateResult:
    """Gate 6 dispatcher (Python P0 + GPT P1).

    Inputs from ``ctx.payload``:
        ``position`` (dict, required), ``unrealized_pnl_r`` (float),
        ``max_loss_r`` (float), ``swap_candidate`` (dict | None),
        ``market_view`` (dict, optional — regime / atr_pct / volume_now),
        ``recent_ticks`` (list, optional).

    Call-efficiency (#15): when ``call_cache`` is supplied (P1 live recalc) the
    GPT call is gated behind a per-position cooldown + context-change check —
    an unchanged context inside the cooldown reuses the prior GPT decision
    (``model_used="python_fast_path"``). The hard loss rail + Q8 swap still
    fire as a Python fast-path *before* this gate, so quality is preserved.

    Fail-open (Q4): missing position or LLM error → Python rules (never KILL).
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

    # Python fast-path: hard loss rail + explicit Q8 swap candidate fire
    # before any GPT call so the LLM cannot delay a stop hit or override
    # the deterministic swap eligibility check.
    if pnl_r <= -abs(max_loss_r):
        return _python_decision(
            pos=pos, pnl_r=pnl_r, max_loss_r=max_loss_r, candidate=candidate_dict,
        )
    if candidate_dict is not None and evaluate_strategy_swap(
        position=pos, candidate=candidate_dict
    ):
        return _python_decision(
            pos=pos, pnl_r=pnl_r, max_loss_r=max_loss_r, candidate=candidate_dict,
        )

    if client is None:
        return _python_decision(
            pos=pos, pnl_r=pnl_r, max_loss_r=max_loss_r, candidate=candidate_dict,
        )

    market_view_obj = ctx.payload.get("market_view") or {}
    market_view = market_view_obj if isinstance(market_view_obj, dict) else {}
    recent_ticks_obj = ctx.payload.get("recent_ticks") or []
    recent_ticks = recent_ticks_obj if isinstance(recent_ticks_obj, list) else []

    # Call-efficiency gate (#15): reuse the prior GPT decision when the context
    # is unchanged inside the cooldown. Hard rails already fired above, so this
    # only short-circuits the supervisory GPT branch (cost, not quality).
    call_ctx: G6CallContext | None = None
    if call_cache is not None and ctx.position_id is not None:
        price_raw = pos.get("last_price")
        if price_raw is None:
            price_raw = pos.get("entry_price", 0.0)
        call_ctx = G6CallContext(
            price=float(price_raw),
            regime=str(market_view.get("regime", "chop")),
            pnl_r=pnl_r,
            now_ts=int(ctx.started_ts),
            tick_idx=int(tick_idx),
        )
        if not should_call_gpt(call_cache, ctx.position_id, call_ctx):
            cached = call_cache.last_decision(ctx.position_id)
            if cached is not None:
                return _reuse_decision(cached, pnl_r)

    system = make_system_prefix(
        role=(
            "Polaris Position Monitor — keep winners open longer (aggressive "
            "bias). Default HOLD unless explicit reason to act. Hard loss rail "
            "is enforced upstream (Python) so every exit you emit is a *signal* "
            "exit, never a stop hit."
        ),
        decision_enum=G6_DECISION_ENUM,
        cell_summary=str(market_view.get("cell_routing", "")),
        baseline_summary=str(market_view.get("baseline", "")),
        recent_trades_summary=str(recent_ticks[:5]),
    )
    p1_root = GPT_P1_MODEL.lower()
    p0_root = GPT_P0_MODEL.lower()
    m = model.lower()
    if m == p1_root or m.startswith(f"{p1_root}-"):
        model_label = "gpt_p1"
    elif m == p0_root or m.startswith(f"{p0_root}-"):
        model_label = "gpt"
    else:
        model_label = "gpt"
    res = await call_gpt(
        client=client,
        system_prefix=system,
        user_prompt=_build_g6_user_prompt(
            pos=pos, pnl_r=pnl_r, max_loss_r=max_loss_r,
            market_view=market_view, recent_ticks=recent_ticks,
        ),
        max_tokens=G6_MAX_TOKENS,
        model=model,
        timeout_sec=DEFAULT_TIMEOUT_SEC,
    )
    # Re-anchor the call cache now that GPT actually ran (resets the cooldown +
    # context windows from this call, regardless of parse outcome). #15.
    if call_cache is not None and call_ctx is not None and ctx.position_id is not None:
        call_cache.record(
            ctx.position_id, call_ctx, decision=str(res.parsed and res.parsed.get("decision") or "HOLD"),
        )
    if res.error or res.parsed is None:
        # Fail-open to Python rules.
        fallback = _python_decision(
            pos=pos, pnl_r=pnl_r, max_loss_r=max_loss_r, candidate=candidate_dict,
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
        )
    parsed = res.parsed
    decision_text = str(parsed.get("decision", "HOLD"))
    decision = _coerce_decision(decision_text)
    if decision is None:
        # Unknown decision — fail-open to Python.
        fallback = _python_decision(
            pos=pos, pnl_r=pnl_r, max_loss_r=max_loss_r, candidate=candidate_dict,
        )
        return GateResult(
            decision=fallback.decision,
            next_gate=fallback.next_gate,
            payload={**fallback.payload, "fallback": "bad_decision",
                     "raw": decision_text},
            model_used=model_label,
            latency_ms=res.latency_ms,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
        )
    # Reject SWAP_STRATEGY when no candidate present (Q8 invariant).
    if decision == GateDecision.SWAP_STRATEGY and candidate_dict is None:
        decision = GateDecision.HOLD
    reason = str(parsed.get("reason", ""))[:200]
    new_stop = parsed.get("new_stop_distance")
    payload: dict[str, Any] = {
        "reason": reason or "gpt_decision",
        "pnl_r": pnl_r,
        "decision_source": model_label,
    }
    if isinstance(new_stop, (int, float)):
        payload["new_stop_distance"] = float(new_stop)
    if decision == GateDecision.SWAP_STRATEGY and candidate_dict is not None:
        payload["swap"] = candidate_dict
        payload["from_strategy"] = pos.get("strategy")
    return GateResult(
        decision=decision,
        next_gate=GATE_ADAPTIVE_EXIT,
        payload=payload,
        model_used=model_label,
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
    )
