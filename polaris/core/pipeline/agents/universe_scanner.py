"""Gate 1 — Universe Scanner (deterministic scored ranker).

Spec source:
- vault/30_components/layer-2-per-gate-pipeline.md (Q3 G1 focus selector)
- vault/10_decisions/ADR-004-per-gate-ai-pipeline.md (Universe Scanner)
- .claude/plans/ai_conductor_architecture_2026-05-30.md (P1 GPT→deterministic cutover)

Behavior:
- Input: active universe rows (``venue`` / ``symbol`` / ``vol_24h_usd`` +
  optional ``atr_24h_pct`` / ``cell_quartile`` / ``hit_rate``).
- Output: 12-48 ticker focus subset, always ``PASS`` (G1 is a focus SELECTOR,
  never a KILL gate).

Cutover (2026-05-30, AI-conductor P1): the GPT branch is REMOVED. The focus list
is now chosen by a DETERMINISTIC scored ranker — no LLM call. The score is a
weighted sum (vol-dominant by construction) and the top-N rows become the focus.

    score = w_vol      * log1p(vol_24h_usd)
          + w_quartile * cell_quartile_score      (top=1 / mid=0.5 / bottom=0)
          + w_rvol     * realized_vol (ATR proxy)  (atr_24h_pct, as a fraction)
          + w_hit_rate * per-symbol recent hit-rate (gate_events; 0 when absent)

BEHAVIOUR PRESERVED: ``log1p(vol)`` is strictly monotonic in vol, so when only
``vol_24h_usd`` is present (today's production universe row) the ranking is
IDENTICAL to the old ``deterministic_top_n`` fallback (top-N by vol). The
secondary terms only nudge ties / enrich the order when those signals exist.

AGGRESSIVE bias untouched: G1 STILL always PASS, coverage stays WIDE (default
N=30, FOCUS_MAX=48), focus is never starved. This is NOT a throttle — no entry
blocked, no size cut. 9-stack is untouched (sizing unchanged).

The ``should_call_gpt_g1`` fingerprint+cooldown logic (``g1_focus_gate``) is
REUSED here as the RECOMPUTE trigger: recompute the scored ranking on a listing
change / top-K turnover >= threshold / cooldown elapse; otherwise reuse the
cached focus (the universe composition has not moved, so the ranking has not).

Weights w1-4 are env-overridable (``POLARIS_G1_W_*``) with conservative,
vol-dominant defaults. They are a /debate calibration target (ai_conductor
needs_debate #3: deterministic-threshold calibration is a trading parameter).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Final

from polaris.core.pipeline.g1_focus_gate import (
    G1FocusCache,
    should_call_gpt_g1,
)
from polaris.core.pipeline.gate_state import (
    GATE_STRATEGY_SIGNAL,
    GateContext,
    GateDecision,
    GateResult,
)

__all__ = [
    "DEFAULT_FOCUS_N",
    "FOCUS_MAX",
    "FOCUS_MIN",
    "RankWeights",
    "deterministic_top_n",
    "scored_top_n",
    "universe_scanner_gate",
]

FOCUS_MIN: Final[int] = 12
FOCUS_MAX: Final[int] = 48
# Default focus width — wide coverage (aggressive bias). 30 reproduces the
# legacy deterministic fallback size exactly. Env-overridable, clamped 12-48.
DEFAULT_FOCUS_N: Final[int] = 30
_ENV_FOCUS_N: Final[str] = "POLARIS_G1_FOCUS_N"

# --- Score weights (env-overridable; /debate calibration target) -----------
# Conservative + VOL-DOMINANT by construction: log1p(vol) is ~17-21 for a
# $30M-$1B universe, dwarfing the bounded secondary terms (quartile 0-1,
# atr-as-fraction ~0.01-0.10, hit-rate 0-1). The secondary weights are
# tie-breakers / enrichers, never enough to invert a real volume gap. Calibrate
# against realized MFE/MAE per ai_conductor needs_debate #3.
_ENV_W_VOL: Final[str] = "POLARIS_G1_W_VOL"
_ENV_W_QUARTILE: Final[str] = "POLARIS_G1_W_QUARTILE"
_ENV_W_RVOL: Final[str] = "POLARIS_G1_W_RVOL"
_ENV_W_HITRATE: Final[str] = "POLARIS_G1_W_HITRATE"

_DEFAULT_W_VOL: Final[float] = 1.0
_DEFAULT_W_QUARTILE: Final[float] = 0.5
_DEFAULT_W_RVOL: Final[float] = 0.3
_DEFAULT_W_HITRATE: Final[float] = 0.5

# cell-matrix quartile label → score (top routes get a small lift).
_QUARTILE_SCORE: Final[dict[str, float]] = {
    "top": 1.0,
    "upper": 0.75,
    "mid": 0.5,
    "lower": 0.25,
    "bottom": 0.0,
}


@dataclass(frozen=True, slots=True)
class RankWeights:
    """G1 scored-ranker weights (env-overridable, /debate calibration target)."""

    vol: float
    quartile: float
    realized_vol: float
    hit_rate: float


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    # Allow 0.0 (disable a term); reject NaN / inf / negative.
    return val if math.isfinite(val) and val >= 0.0 else default


def _load_weights() -> RankWeights:
    """Read the score weights from the environment (defaults if unset)."""
    return RankWeights(
        vol=_env_float(_ENV_W_VOL, _DEFAULT_W_VOL),
        quartile=_env_float(_ENV_W_QUARTILE, _DEFAULT_W_QUARTILE),
        realized_vol=_env_float(_ENV_W_RVOL, _DEFAULT_W_RVOL),
        hit_rate=_env_float(_ENV_W_HITRATE, _DEFAULT_W_HITRATE),
    )


def _focus_target_n() -> int:
    """Resolve the focus width (env-overridable), clamped to [FOCUS_MIN, FOCUS_MAX]."""
    raw = os.environ.get(_ENV_FOCUS_N)
    n = DEFAULT_FOCUS_N
    if raw is not None and raw != "":
        try:
            n = int(float(raw))
        except ValueError:
            n = DEFAULT_FOCUS_N
    return max(FOCUS_MIN, min(FOCUS_MAX, n))


def _quartile_score(row: dict[str, Any]) -> float:
    """Map a cell-matrix quartile label (or numeric) to a 0-1 score; 0 if absent."""
    q = row.get("cell_quartile")
    if q is None:
        q = row.get("quartile")
    if isinstance(q, str):
        return _QUARTILE_SCORE.get(q.strip().lower(), 0.0)
    if isinstance(q, (int, float)) and math.isfinite(float(q)):
        return max(0.0, min(1.0, float(q)))
    return 0.0


def _realized_vol(row: dict[str, Any]) -> float:
    """Realized-vol proxy as a fraction; reads ATR%/realized_vol, 0 if absent."""
    for key in ("realized_vol", "atr_24h_pct", "atr_pct"):
        v = row.get(key)
        if isinstance(v, (int, float)) and math.isfinite(float(v)):
            val = float(v)
            # ATR is stored as a percent (e.g. 4.0 == 4%); fold to a fraction so
            # the term stays bounded ~0.01-0.10 and cannot dominate vol.
            return max(0.0, val / 100.0) if key != "realized_vol" else max(0.0, val)
    return 0.0


def _hit_rate(row: dict[str, Any]) -> float:
    """Per-symbol recent hit-rate in [0, 1]; 0 if absent (cold / no gate_events)."""
    v = row.get("hit_rate")
    if isinstance(v, (int, float)) and math.isfinite(float(v)):
        return max(0.0, min(1.0, float(v)))
    return 0.0


def _score(row: dict[str, Any], w: RankWeights) -> float:
    vol = float(row.get("vol_24h_usd", 0.0) or 0.0)
    return (
        w.vol * math.log1p(max(0.0, vol))
        + w.quartile * _quartile_score(row)
        + w.realized_vol * _realized_vol(row)
        + w.hit_rate * _hit_rate(row)
    )


def deterministic_top_n(
    universe: list[dict[str, Any]],
    *,
    n: int = DEFAULT_FOCUS_N,
) -> list[dict[str, Any]]:
    """Top-``n`` rows of ``universe`` by ``vol_24h_usd`` desc.

    Retained as the legacy vol-only ranking + the < FOCUS_MIN augment source.
    With only ``vol_24h_usd`` present this matches ``scored_top_n`` exactly.
    """
    if not universe:
        return []
    sorted_rows = sorted(
        universe, key=lambda r: float(r.get("vol_24h_usd", 0.0)), reverse=True
    )
    return sorted_rows[: max(1, n)]


def scored_top_n(
    universe: list[dict[str, Any]],
    *,
    n: int = DEFAULT_FOCUS_N,
    weights: RankWeights | None = None,
) -> list[dict[str, Any]]:
    """Top-``n`` rows of ``universe`` by the G1 composite score (desc).

    Deterministic. When only ``vol_24h_usd`` is present the score is monotonic
    in vol, so the ordering is identical to ``deterministic_top_n``.
    """
    if not universe:
        return []
    w = weights if weights is not None else _load_weights()
    sorted_rows = sorted(universe, key=lambda r: _score(r, w), reverse=True)
    return sorted_rows[: max(1, n)]


def _select_focus(universe: list[dict[str, Any]]) -> list[str]:
    """Run the scored ranker → ``venue:symbol`` focus tokens (clamped 12-48)."""
    target = _focus_target_n()
    ranked = scored_top_n(universe, n=target)
    focus = [f"{r.get('venue')}:{r.get('symbol')}" for r in ranked]
    # Augment from top-by-vol so we always emit at least FOCUS_MIN when the
    # universe is large enough (best-effort: cannot exceed what exists).
    if len(focus) < FOCUS_MIN:
        seen = set(focus)
        for r in deterministic_top_n(universe, n=FOCUS_MAX):
            tok = f"{r.get('venue')}:{r.get('symbol')}"
            if tok in seen:
                continue
            focus.append(tok)
            seen.add(tok)
            if len(focus) >= FOCUS_MIN:
                break
    return focus[:FOCUS_MAX]


async def universe_scanner_gate(
    ctx: GateContext,
    *,
    universe: list[dict[str, Any]] | None = None,
    focus_cache: G1FocusCache | None = None,
    now_ts: int | None = None,
) -> GateResult:
    """Gate 1 dispatcher — deterministic scored-ranker focus selector.

    Reads ``universe`` from ``ctx.payload`` if not passed explicitly (test
    injection). Always returns a ``PASS`` GateResult carrying a ``focus`` list
    under ``payload`` — the orchestrator passes it forward.

    RECOMPUTE trigger (efficiency only; reuses ``should_call_gpt_g1``): when a
    ``focus_cache`` is supplied the ranking is recomputed only on a cold cache /
    listing change / top-K turnover / cooldown elapse; otherwise the cached
    focus is reused (the composition has not moved, so the ranking has not).
    G1 STILL always PASS and a focus list is emitted every cycle.
    """
    uni = universe if universe is not None else list(ctx.payload.get("universe", []))
    ts = now_ts if now_ts is not None else int(ctx.started_ts)

    # RECOMPUTE trigger: reuse the cached focus when the composition is
    # materially unchanged inside the cooldown. Conservative — ``should_call_
    # gpt_g1`` returns True on a cold cache / new listing / delisting /
    # >= threshold top-K turnover / cooldown elapse, so focus is never starved.
    if focus_cache is not None and not should_call_gpt_g1(focus_cache, uni, now_ts=ts):
        cached = focus_cache.last_focus()
        if cached:
            return GateResult(
                decision=GateDecision.PASS,
                next_gate=GATE_STRATEGY_SIGNAL,
                payload={"focus": cached, "focus_source": "cache"},
                model_used="python",
                latency_ms=0,
            )

    focus = _select_focus(uni)

    # Re-anchor the cache to this composition + fresh focus so the next
    # unchanged scan inside the cooldown reuses it (no recompute).
    if focus_cache is not None:
        focus_cache.record(uni, focus=focus, now_ts=ts)

    return GateResult(
        decision=GateDecision.PASS,
        next_gate=GATE_STRATEGY_SIGNAL,
        payload={"focus": focus},
        model_used="python",
        latency_ms=0,
    )
