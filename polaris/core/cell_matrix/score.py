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
    "REGIME_RANK_PENALTY_DAMPEN",
    "REGIME_RANK_PENALTY_NEUTRAL",
    "apply_regime_alignment",
    "compute_avg_pnl_r",
    "compute_cell_score",
    "decay_factor",
    "posterior_tilt",
    "regime_alignment_mult",
    "regime_rank_penalty",
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
# Ids verified against polaris.strategies.STRATEGY_REGISTRY (all live ids) — so
# every live trend strategy is regime-scored (an omitted id would be silently
# scored neutral).
_TREND_STRATEGIES: Final[frozenset[str]] = frozenset(
    {
        "spot_donchian",
        "fx_breakout_basket",
        "session_breakout",
        "xau_indices_trend",
        "volume_burst",
    }
)
# Counter-trend / mean-reversion: edge is captured in range-bound chop.
# (fx_range_fade was un-registered in the strategy-wave1 restructure — removed
# here so this set stays in sync with STRATEGY_REGISTRY: every live counter-trend
# strategy is regime-scored, and no dead id remains.)
_COUNTER_TREND_STRATEGIES: Final[frozenset[str]] = frozenset(
    {
        "rsi_bb_pullback",
    }
)

_TREND_REGIMES: Final[frozenset[str]] = frozenset({"bull_trend", "bear_trend"})
_CHOP_REGIME: Final[str] = "chop"
_BEAR_TREND_REGIME: Final[str] = "bear_trend"

# Long-only venues (OKX SPOT, Alpaca equity) can ONLY hold longs — there is no
# short leg. A trend strategy in a DOWN-trend (bear_trend) is therefore HEADWIND
# for these venues, not edge, so it is DAMPENED rather than AMPLIFIED. Capital
# CFD is long/short, so its trend strategies short the bear and KEEP AMPLIFY
# (its bear edge is the short leg). Verified long-only: cci_reversion.py side
# fixed "long"; only Capital strategies emit shorts (fx_range_fade short OK).
_LONG_ONLY_VENUES: Final[frozenset[str]] = frozenset({"okx", "alpaca"})


def regime_alignment_mult(
    *, strategy: str, regime: str, exchange: str | None = None
) -> float:
    """Deterministic regime/strategy fit multiplier (MVP — not a full HMM).

    Trend strategies are amplified in trend regimes and dampened in chop;
    counter-trend (``rsi_bb_pullback``) is amplified in chop and dampened in
    trend. ``crisis`` and any unknown regime/strategy stay neutral — no edge
    claim. Dampen is a redistribution (strictly positive), never an off-switch.

    Venue/side-aware bear headwind (P-A): in ``bear_trend`` a trend strategy on
    a long-only venue (``exchange`` in :data:`_LONG_ONLY_VENUES` — OKX SPOT /
    Alpaca) is DAMPENED, not amplified, because a long-only book cannot short
    the down-trend (a trend long there is headwind = "going to cash is the
    edge", a strictly-positive score-tilt, NOT a block / zero). Capital CFD
    (long/short) keeps AMPLIFY in ``bear_trend`` — its short leg IS the bear
    edge. ``exchange=None`` / an unknown venue makes no venue-specific claim and
    preserves the family default (AMPLIFY in bear); the production routing /
    emit-priority call sites pass the cell's ``exchange``, so the fix fires
    there. ``bull_trend`` / ``chop`` / ``crisis`` mappings are unchanged.
    """
    if regime in _TREND_REGIMES:
        if strategy in _TREND_STRATEGIES:
            if regime == _BEAR_TREND_REGIME and exchange in _LONG_ONLY_VENUES:
                return REGIME_ALIGN_DAMPEN
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


def apply_regime_alignment(
    score: float, *, strategy: str, regime: str, exchange: str | None = None
) -> float:
    """Scale a cell score by the regime/strategy alignment multiplier.

    Purely multiplicative, so sign is preserved (a losing cell stays losing)
    and a zero score stays zero. Existing ``compute_cell_score`` contract is
    untouched — callers opt in to alignment by wrapping the score with this.
    ``exchange`` threads through to the venue-aware bear headwind (see
    :func:`regime_alignment_mult`).
    """
    return score * regime_alignment_mult(
        strategy=strategy, regime=regime, exchange=exchange
    )


# ---------------------------------------------------------------------------
# Regime → G2 EMIT-PRIORITY (ticker-tailored, regime-first POOL ordering)
# ---------------------------------------------------------------------------

REGIME_RANK_PENALTY_NEUTRAL: Final[float] = 0.10
"""Rank-down for a strategy with NO regime claim (crisis / unknown / cold).

Strictly between the best-fit (0.0) and the mis-fit (DAMPEN) penalty so an
unclassified ticker/strategy competes on neutral footing — never demoted into
oblivion (the cold-start trap the /debate flagged). Well UNDER one PDT step
(1.0), so PDT integrity ranking always dominates regime fit.
"""

REGIME_RANK_PENALTY_DAMPEN: Final[float] = 0.20
"""Rank-down for a MIS-regime strategy on this ticker (ranked DOWN, never out).

A mis-fit strategy still emits, still flows, still sized normally — it is just
sorted LATER in the per-tick batch (a head-start, not a hard precedence — see
:func:`regime_rank_penalty`). Still well under one PDT step so it never
overrides a PDT integrity demotion.
"""


def regime_rank_penalty(
    *, strategy: str, regime: str, exchange: str | None = None
) -> float:
    """Ticker-tailored G2 emit-priority penalty (regime-first POOL ordering).

    Re-encodes the SAME ``regime_alignment_mult`` fitness SSOT as a NON-NEGATIVE
    ranking-down number (the :class:`PipelineTaskSpec.rank_penalty` contract):

      * AMPLIFY (strategy's edge fits THIS ticker's live regime) → ``0.0`` — the
        best-fit POOL member sorts FIRST in the per-tick batch.
      * NEUTRAL (crisis / unknown / cold) → ``REGIME_RANK_PENALTY_NEUTRAL``.
      * DAMPEN (mis-regime)              → ``REGIME_RANK_PENALTY_DAMPEN`` — sorted
        LATER, but STILL emits / flows / sized (flow_not_block).

    This is the "동적할당 POOL" expressed as a 3-tier PRIORITY, never a gate: no
    signal is dropped, skipped, or size-cut, and the penalty is NEVER a T4 sizing
    multiplier (notional is byte-identical for a given signal regardless of its
    rank — ``focus/assignment`` is not a sizing input, 9-stack untouched). An
    unknown family/regime degrades to NEUTRAL (total function, never raises).

    Effect is the per-tick batch SORT (``order_specs_by_rank``): a lower penalty
    is created earlier in the supervised TaskGroup, a structural HEAD-START into
    the concurrent gate fan-out. It is a best-effort priority nudge, NOT a
    guaranteed risk-budget reservation order (the gate awaits interleave, so the
    final reservation is still gate-completion-ordered).
    """
    mult = regime_alignment_mult(strategy=strategy, regime=regime, exchange=exchange)
    if mult >= REGIME_ALIGN_AMPLIFY:
        return 0.0
    if mult <= REGIME_ALIGN_DAMPEN:
        return REGIME_RANK_PENALTY_DAMPEN
    return REGIME_RANK_PENALTY_NEUTRAL


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
