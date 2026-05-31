"""Layer 4 — pure score helpers (EWMA decay, T11 score, warmup shrinkage).

Spec source: vault/30_components/layer-4-cell-matrix.md (Q2, Q4, Q5).
"""

from __future__ import annotations

import math
from typing import Final

from polaris.core.cell_matrix.schema import (
    CELL_BASELINE_N,
    CELL_DECAY_HALF_LIFE_SEC,
    CELL_MIN_LIVE_N,
    CELL_SHRINKAGE_N,
)

__all__ = [
    "POSTERIOR_TILT_CEIL",
    "POSTERIOR_TILT_FLOOR",
    "POSTERIOR_TILT_MIN_N",
    "REGIME_ALIGN_AMPLIFY",
    "REGIME_ALIGN_DAMPEN",
    "REGIME_ALIGN_NEUTRAL",
    "apply_exponential_decay",
    "apply_regime_alignment",
    "compute_avg_pnl_r",
    "compute_cell_score",
    "decay_factor",
    "posterior_tilt",
    "regime_alignment_mult",
    "resolve_effective_score",
]

_LN2: Final[float] = math.log(2.0)

# ---------------------------------------------------------------------------
# Regime → cell-score alignment (MVP, deterministic vol/trend fit)
# ---------------------------------------------------------------------------

REGIME_ALIGN_AMPLIFY: Final[float] = 1.25
"""Score weight when the strategy's edge type matches the live regime."""

REGIME_ALIGN_NEUTRAL: Final[float] = 1.0
"""No directional claim (crisis / unknown regime or strategy)."""

REGIME_ALIGN_DAMPEN: Final[float] = 0.8
"""Misaligned strategy/regime — down-weighted, never zeroed (redistribution)."""

# Trend-following / breakout family: edge is captured when price trends.
_TREND_STRATEGIES: Final[frozenset[str]] = frozenset(
    {
        "tsmom",
        "spot_donchian",
        "fx_breakout_basket",
        "session_breakout",
        "xau_indices_trend",
        "volume_burst",
    }
)
# Counter-trend / mean-reversion: edge is captured in range-bound chop.
_COUNTER_TREND_STRATEGIES: Final[frozenset[str]] = frozenset({"rsi_bb_pullback"})

_TREND_REGIMES: Final[frozenset[str]] = frozenset({"bull_trend", "bear_trend"})
_CHOP_REGIME: Final[str] = "chop"


def regime_alignment_mult(*, strategy: str, regime: str) -> float:
    """Deterministic regime/strategy fit multiplier (MVP — not a full HMM).

    Trend strategies are amplified in trend regimes and dampened in chop;
    counter-trend (``rsi_bb_pullback``) is amplified in chop and dampened in
    trend. ``crisis`` and any unknown regime/strategy stay neutral — no edge
    claim. Dampen is a redistribution (strictly positive), never an off-switch.
    """
    if regime in _TREND_REGIMES:
        if strategy in _TREND_STRATEGIES:
            return REGIME_ALIGN_AMPLIFY
        if strategy in _COUNTER_TREND_STRATEGIES:
            return REGIME_ALIGN_DAMPEN
        return REGIME_ALIGN_NEUTRAL
    if regime == _CHOP_REGIME:
        if strategy in _COUNTER_TREND_STRATEGIES:
            return REGIME_ALIGN_AMPLIFY
        if strategy in _TREND_STRATEGIES:
            return REGIME_ALIGN_DAMPEN
        return REGIME_ALIGN_NEUTRAL
    return REGIME_ALIGN_NEUTRAL


def apply_regime_alignment(score: float, *, strategy: str, regime: str) -> float:
    """Scale a cell score by the regime/strategy alignment multiplier.

    Purely multiplicative, so sign is preserved (a losing cell stays losing)
    and a zero score stays zero. Existing ``compute_cell_score`` contract is
    untouched — callers opt in to alignment by wrapping the score with this.
    """
    return score * regime_alignment_mult(strategy=strategy, regime=regime)


# ---------------------------------------------------------------------------
# Posterior tilt → routing-score conviction scale (continuous, bounded)
# ---------------------------------------------------------------------------

POSTERIOR_TILT_FLOOR: Final[float] = 0.5
"""Routing-score tilt at p_pos = 0 (max anti-edge evidence) — never 0."""

POSTERIOR_TILT_CEIL: Final[float] = 1.5
"""Routing-score tilt at p_pos = 1 (max +edge evidence)."""

POSTERIOR_TILT_MIN_N: Final[int] = 20
"""Below this posterior n_samples the tilt is neutral 1.0 (cold = no claim).

Mirrors ``polaris.core.rotation.POSTERIOR_MIN_N`` — a posterior is only trusted
to tilt routing once it has seen enough cost-adjusted closes.
"""


def posterior_tilt(*, p_pos: float, n_samples: int) -> float:
    """Continuous routing-score conviction scale from the learner posterior.

    ``p_pos = P(expectancy > 0)`` (the NIG marginal-t tail, posterior.py). The
    tilt linearly maps ``p_pos ∈ [0, 1]`` onto ``[FLOOR, CEIL]`` so that:

      * ``p_pos = 0.5`` (no evidence)          → 1.0 (neutral),
      * ``p_pos → 1``   (validated +edge)      → ``CEIL`` (amplify, ≤ 1.5),
      * ``p_pos → 0``   (anti-edge)            → ``FLOOR`` (dampen, ≥ 0.5).

    Returns 1.0 EXACTLY when ``n_samples < POSTERIOR_TILT_MIN_N`` (cold / sparse
    cell — no posterior claim, neither amplified nor suppressed) so exploration
    of a cold cell is never throttled. Strictly positive, never an off-switch
    (FLOOR > 0). Out-of-range / non-finite ``p_pos`` clamps to ``[0, 1]`` so a
    corrupt row degrades to a bounded tilt rather than crashing the router.
    """
    if n_samples < POSTERIOR_TILT_MIN_N:
        return 1.0
    if not math.isfinite(p_pos):
        return 1.0
    p = min(1.0, max(0.0, p_pos))
    return POSTERIOR_TILT_FLOOR + (POSTERIOR_TILT_CEIL - POSTERIOR_TILT_FLOOR) * p


def decay_factor(*, elapsed_sec: float, half_life_sec: float = CELL_DECAY_HALF_LIFE_SEC) -> float:
    """Return ``exp(-elapsed × ln 2 / half_life)``.

    ``elapsed_sec < 0`` clamps to 0 so clock skew never inflates state.
    ``half_life_sec <= 0`` raises — silent fallback would mask config rot.
    """
    if half_life_sec <= 0.0:
        raise ValueError("half_life_sec must be > 0")
    if not math.isfinite(elapsed_sec):
        raise ValueError("elapsed_sec must be finite")
    if elapsed_sec <= 0.0:
        return 1.0
    return math.exp(-elapsed_sec * _LN2 / half_life_sec)


def apply_exponential_decay(
    value: float,
    *,
    elapsed_sec: float,
    half_life_sec: float = CELL_DECAY_HALF_LIFE_SEC,
) -> float:
    """Decay a scalar by EWMA factor."""
    return value * decay_factor(elapsed_sec=elapsed_sec, half_life_sec=half_life_sec)


def compute_avg_pnl_r(*, pnl_r_sum_eff: float, n_eff: float) -> float:
    """``avg_pnl_r = pnl_r_sum_eff / n_eff`` with safe zero handling."""
    if n_eff <= 0.0:
        return 0.0
    return pnl_r_sum_eff / n_eff


def compute_cell_score(
    *,
    avg_pnl_r: float,
    n_eff: float,
    baseline_n: float = CELL_BASELINE_N,
) -> float:
    """T11 cell-confidence score.

    ``score = avg_pnl_r × √n_eff / baseline_n``.

    Floor 0 not applied — losing cells keep their negative score so the
    bottom-quartile router can find them.
    """
    if baseline_n <= 0.0:
        raise ValueError("baseline_n must be > 0")
    if n_eff <= 0.0:
        return 0.0
    if not (math.isfinite(avg_pnl_r) and math.isfinite(n_eff)):
        return 0.0
    return avg_pnl_r * math.sqrt(n_eff) / baseline_n


def resolve_effective_score(
    *,
    cell_score: float,
    cell_n_eff: float,
    parent3_score: float | None,
    parent2_score: float | None,
    shrinkage_n: float = CELL_SHRINKAGE_N,
    min_live_n: float = CELL_MIN_LIVE_N,
) -> float:
    """Apply warmup shrinkage to a cell score.

    | n_eff range          | result                                            |
    |----------------------|---------------------------------------------------|
    | n_eff < min_live_n   | 0.0 (caller must route ×1.0 neutral on this)      |
    | min_live_n ≤ n < 20  | blend ``(n/20)·cell + ((20-n)/20)·parent``        |
    | n ≥ shrinkage_n      | cell_score unchanged                              |

    Parent fallback order: parent3 → parent2 → 0.0. Negative scores preserved.
    """
    if shrinkage_n <= 0.0:
        raise ValueError("shrinkage_n must be > 0")
    if cell_n_eff < min_live_n:
        return 0.0
    if cell_n_eff >= shrinkage_n:
        return cell_score
    if parent3_score is not None:
        parent = parent3_score
    elif parent2_score is not None:
        parent = parent2_score
    else:
        parent = 0.0
    weight_cell = cell_n_eff / shrinkage_n
    weight_parent = 1.0 - weight_cell
    return weight_cell * cell_score + weight_parent * parent
