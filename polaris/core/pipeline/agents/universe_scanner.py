"""Gate 1 — Universe Scanner (GPT P0).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G1 + Q5 latency 1200ms)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Universe Scanner)

Behavior:
- Input: 270 active universe + cell_matrix score snapshot.
- Output: 12-48 ticker focus subset.
- Fail-open: timeout / parse error → top 30 by 24h vol (Python deterministic).

Migration (2026-05-07): Anthropic Haiku → OpenAI GPT (Jin mandate). Aggressive
bias preserved — fail-OPEN keeps coverage broad even when GPT errors.
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
    GATE_STRATEGY_SIGNAL,
    GateContext,
    GateDecision,
    GateResult,
)

__all__ = [
    "FOCUS_MAX",
    "FOCUS_MIN",
    "SCANNER_MAX_TOKENS",
    "SCANNER_PROMPT_UNIVERSE_CAP",
    "deterministic_top_n",
    "universe_scanner_gate",
]

FOCUS_MIN: Final[int] = 12
FOCUS_MAX: Final[int] = 48
DEFAULT_FALLBACK_N: Final[int] = 30
# Q5 latency budget — 400 token output keeps the focus list under the
# 1.2 s G1 ceiling.
SCANNER_MAX_TOKENS: Final[int] = 400
# Cap rows surfaced to the prompt so cost scales sub-linearly with universe.
SCANNER_PROMPT_UNIVERSE_CAP: Final[int] = 300


def deterministic_top_n(
    universe: list[dict[str, Any]],
    *,
    n: int = DEFAULT_FALLBACK_N,
) -> list[dict[str, Any]]:
    """Top-``n`` rows of ``universe`` by ``vol_24h_usd`` desc.

    Used as the deterministic fallback when Haiku output is unavailable.
    """
    if not universe:
        return []
    sorted_rows = sorted(
        universe, key=lambda r: float(r.get("vol_24h_usd", 0.0)), reverse=True
    )
    return sorted_rows[: max(1, n)]


def _build_user_prompt(universe: list[dict[str, Any]], cell_summary: str) -> str:
    rows = "\n".join(
        f"- {r.get('venue')}:{r.get('symbol')} vol=${float(r.get('vol_24h_usd', 0)):,.0f}"
        for r in universe[:SCANNER_PROMPT_UNIVERSE_CAP]
    )
    return (
        f"# Universe ({len(universe)} active)\n{rows}\n\n"
        f"# Cell snapshot\n{cell_summary}\n\n"
        f"Select {FOCUS_MIN}-{FOCUS_MAX} tickers for this cycle. Aggressive bias.\n"
        'Output JSON: {"focus": ["okx:BTC-USDT", ...]}'
    )


async def universe_scanner_gate(
    ctx: GateContext,
    *,
    client: Any | None = None,
    universe: list[dict[str, Any]] | None = None,
    cell_summary: str = "",
) -> GateResult:
    """Gate 1 dispatcher.

    Reads universe + cell_summary from ``ctx.payload`` if not passed explicitly
    (allows test injection). Returns a GateResult that always includes a
    ``focus`` list under ``payload`` — the orchestrator passes it forward.
    """
    uni = universe if universe is not None else list(ctx.payload.get("universe", []))
    summary = cell_summary or ctx.payload.get("cell_summary", "")

    # Deterministic fallback: no client → top-N by vol.
    if client is None:
        fallback = deterministic_top_n(uni)
        return GateResult(
            decision=GateDecision.PASS,
            next_gate=GATE_STRATEGY_SIGNAL,
            payload={"focus": [f"{r.get('venue')}:{r.get('symbol')}" for r in fallback]},
            model_used="python",
            latency_ms=0,
        )

    system = make_system_prefix(
        role="Polaris Universe Scanner — pick aggressive focus list.",
        decision_enum=["focus_list"],
        cell_summary=summary,
        baseline_summary="(omitted in G1)",
        recent_trades_summary="(omitted in G1)",
    )
    prompt = _build_user_prompt(uni, summary)
    res: GPTCallResult = await call_gpt(
        client=client,
        system_prefix=system,
        user_prompt=prompt,
        max_tokens=SCANNER_MAX_TOKENS,
        timeout_sec=DEFAULT_TIMEOUT_SEC,
    )

    if res.error or res.parsed is None or not isinstance(res.parsed.get("focus"), list):
        # Fail-open: previous focus-or-fallback (per L2 Q4 — G1 = aggressive coverage).
        fallback = deterministic_top_n(uni)
        return GateResult(
            decision=GateDecision.PASS,
            next_gate=GATE_STRATEGY_SIGNAL,
            payload={
                "focus": [f"{r.get('venue')}:{r.get('symbol')}" for r in fallback],
                "fallback_reason": res.error or "gpt_no_focus",
            },
            model_used="python",
            latency_ms=res.latency_ms,
            error=res.error,
        )

    focus_list = [str(t) for t in res.parsed["focus"]][:FOCUS_MAX]
    if len(focus_list) < FOCUS_MIN:
        # Augment with top-by-vol so we always emit at least FOCUS_MIN.
        seen = set(focus_list)
        for r in deterministic_top_n(uni, n=FOCUS_MAX):
            tok = f"{r.get('venue')}:{r.get('symbol')}"
            if tok in seen:
                continue
            focus_list.append(tok)
            if len(focus_list) >= FOCUS_MIN:
                break

    return GateResult(
        decision=GateDecision.PASS,
        next_gate=GATE_STRATEGY_SIGNAL,
        payload={"focus": focus_list},
        model_used="gpt",
        latency_ms=res.latency_ms,
    )
