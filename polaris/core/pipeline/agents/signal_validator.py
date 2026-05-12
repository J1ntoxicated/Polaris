"""Gate 3 — Signal Validator (GPT P0, fail-closed).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G3 + Q4 fail-closed + Q6 prompt)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Signal Validator)

Behavior:
- Input: raw_signal + cell_matrix routing summary + ticker baseline + recent same-symbol trades.
- Output: PASS / KILL / MODIFY. MODIFY carries ``strength_scalar in [0.5, 1.5]``.
- Fail-closed: timeout / parse error → KILL.

Migration (2026-05-07): Anthropic Haiku → OpenAI GPT (Jin mandate).
Aggressive bias preserved — no defensive throttle on PASS rate.
"""

from __future__ import annotations

from typing import Any, Final

from polaris.core.pipeline.agents._gpt_client import (
    DEFAULT_TIMEOUT_SEC,
    GPTCallResult,
    call_gpt,
    make_system_prefix,
)
from polaris.core.pipeline.gate_state import (
    GATE_PRE_ENTRY_WATCHER,
    GateContext,
    GateDecision,
    GateResult,
)

__all__ = [
    "MODIFY_MAX",
    "MODIFY_MIN",
    "VALIDATOR_MAX_TOKENS",
    "VALIDATOR_RECENT_TRADES_MAX",
    "signal_validator_gate",
]

MODIFY_MIN: Final[float] = 0.5
MODIFY_MAX: Final[float] = 1.5
# Q6 G3 token budget for PASS/KILL/MODIFY response.
VALIDATOR_MAX_TOKENS: Final[int] = 250
# Recent same-symbol trades cap surfaced to the validator prompt.
VALIDATOR_RECENT_TRADES_MAX: Final[int] = 5

_DECISION_TOKENS = {"PASS", "KILL", "MODIFY"}


def _build_user_prompt(
    *,
    raw_signal: dict[str, Any],
    cell_routing: dict[str, Any],
    baseline: dict[str, Any],
    recent_trades: list[dict[str, Any]],
) -> str:
    rt_lines = "\n".join(
        f"- {t.get('ts')}: pnl_r={t.get('pnl_r')} won={t.get('won')}"
        for t in recent_trades[:VALIDATOR_RECENT_TRADES_MAX]
    )
    return (
        f"# Raw signal\n{raw_signal}\n"
        f"# Cell routing\n{cell_routing}\n"
        f"# Baseline\n{baseline}\n"
        f"# Recent same-symbol\n{rt_lines}\n"
        'Output JSON: {"decision": "PASS|KILL|MODIFY", "strength_scalar": 1.0, '
        '"thesis": "..."}'
    )


def _validate_decision(parsed: dict[str, Any]) -> tuple[GateDecision, float] | None:
    """Schema-validate the GPT output. None = invalid → fail-closed KILL."""
    decision = str(parsed.get("decision", "")).upper()
    if decision not in _DECISION_TOKENS:
        return None
    raw_scalar = parsed.get("strength_scalar", 1.0)
    try:
        scalar = float(raw_scalar)
    except (TypeError, ValueError):
        scalar = 1.0
    # Clamp to [MODIFY_MIN, MODIFY_MAX] (GPT is allowed only the 0.5-1.5 range).
    scalar = max(MODIFY_MIN, min(MODIFY_MAX, scalar))
    if decision == "PASS":
        return GateDecision.PASS, 1.0
    if decision == "KILL":
        return GateDecision.KILL, 0.0
    return GateDecision.MODIFY, scalar


async def signal_validator_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
) -> GateResult:
    """Gate 3 dispatcher (GPT validator).

    Reads inputs from ``ctx.payload``:
        ``raw_signal``, ``cell_routing``, ``baseline``, ``recent_trades``.
    Fail-closed: any unhandled error / non-conformant output → KILL.
    """
    raw_signal = ctx.payload.get("raw_signal", {})
    cell_routing = ctx.payload.get("cell_routing", {})
    baseline = ctx.payload.get("baseline", {})
    recent = ctx.payload.get("recent_trades", [])

    if not isinstance(raw_signal, dict) or not raw_signal:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "missing_raw_signal"},
            model_used="python",
        )

    if client is None:
        # Fail-closed without LLM → KILL (entry-side gate, Q4 spec).
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "no_gpt_client"},
            model_used="python",
        )

    system = make_system_prefix(
        role=(
            "You are a Signal Validator gate in Polaris v2 paper trading "
            "system."
        ),
        decision_enum=["PASS", "KILL", "MODIFY"],
        cell_summary=str(cell_routing),
        baseline_summary=str(baseline),
        recent_trades_summary=str(recent[:VALIDATOR_RECENT_TRADES_MAX]),
    )
    prompt = _build_user_prompt(
        raw_signal=raw_signal,
        cell_routing=cell_routing,
        baseline=baseline,
        recent_trades=recent,
    )
    res: GPTCallResult = await call_gpt(
        client=client,
        system_prefix=system,
        user_prompt=prompt,
        max_tokens=VALIDATOR_MAX_TOKENS,
        timeout_sec=DEFAULT_TIMEOUT_SEC,
    )

    if res.error or res.parsed is None:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "gpt_error", "error": res.error},
            model_used="gpt",
            latency_ms=res.latency_ms,
            error=res.error,
        )

    decoded = _validate_decision(res.parsed)
    if decoded is None:
        return GateResult(
            decision=GateDecision.KILL,
            next_gate=None,
            payload={"reason": "gpt_schema_violation", "raw": res.text[:200]},
            model_used="gpt",
            latency_ms=res.latency_ms,
        )
    decision, scalar = decoded
    if decision == GateDecision.KILL:
        return GateResult(
            decision=decision,
            next_gate=None,
            payload={"reason": "validator_kill"},
            model_used="gpt",
            latency_ms=res.latency_ms,
        )
    return GateResult(
        decision=decision,
        next_gate=GATE_PRE_ENTRY_WATCHER,
        payload={
            "validated_signal": {
                **raw_signal,
                "strength_scalar": scalar,
            }
        },
        model_used="gpt",
        latency_ms=res.latency_ms,
    )
