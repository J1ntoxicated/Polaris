"""fee-split v0 (group GROSS) — winsorized weighted t-LCB over a gross-edge
sample series.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Pure
math — no DB, no I/O, never touches sizing/9-stack/-1.0R rail. This module
is the SHADOW-side statistical primitive for the fee-split judgment
(vault/50_research/debates/fee_split_judgment_2026-07-10.md, R2 item 2):
"LCB = winsorized weighted t-LCB, 신뢰도 60%->80% 램프(N_eff 12->20)".

v0 scope note: nothing in this module is wired into a live trading decision.
The survival FSM (``polaris.core.classes.transition``) keeps consuming the
EXISTING fee-normalized ``score_f_events.score_contrib`` unchanged — see the
module docstring of ``shadow_divergence.py`` for the full v0-vs-v1 boundary.

Statistical building blocks (all pure, stdlib-only — no numpy/scipy new
dependency, matching this project's existing ``statistics``-module idiom):

- ``winsorize``: clip outliers to [median - 3*1.4826*MAD, median + 3*1.4826*MAD]
  (1.4826 = the MAD->sigma conversion constant for a normal distribution).
- ``time_decay_weight``: w = exp(-age/H) — the churn-mitigation time-weighted
  window (R2 item 3). NEVER a frequency/count throttle — every close counts,
  just recency-weighted (flow_not_block).
- ``effective_sample_size``: Kish's N_eff = (sum w)^2 / sum(w^2) — the natural
  quantity the confidence ramp and t-distribution degrees-of-freedom key off.
- ``confidence_for_n_eff``: linear ramp 60%->80% as N_eff goes 12->20 (R2
  item 2); N_eff<12 -> None (no verdict — the survival-FSM-side caller must
  treat that as "hold", never as a demotion trigger — cold-start non-
  starvation, "N<12 전이 금지(베이스라인 거래 지속)").
- ``compute_gross_lcb``: composes the above into one lower-confidence-bound
  estimate over a caller-supplied sample series.

The one-sided Student-t critical value uses the Fisher-Cornish asymptotic
expansion (Cornish & Fisher 1938 / Fisher & Cornish 1960) in terms of the
standard-normal quantile (``statistics.NormalDist().inv_cdf``) — accurate to
within ~1e-3 for df>=10, which covers this module's df range (N_eff 12-30 ->
df 11-29). This avoids adding numpy/scipy as a new project dependency.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "CONFIDENCE_MAX",
    "CONFIDENCE_MIN",
    "N_EFF_CAP",
    "N_EFF_CONFIDENCE_FULL",
    "N_EFF_MIN_FLOOR",
    "WINSOR_MAD_K",
    "GrossEdgeSample",
    "GrossLcbResult",
    "compute_gross_lcb",
    "confidence_for_n_eff",
    "effective_sample_size",
    "time_decay_weight",
    "weighted_percentile",
    "winsorize",
]

# MAD->sigma conversion constant for a normal distribution (1/Phi^-1(0.75)).
_MAD_TO_SIGMA: float = 1.4826
WINSOR_MAD_K: float = 3.0

# N_eff ramp (R2 item 2): below the floor -> no verdict (cold-start
# non-starvation is the CALLER's job — this module only reports n_eff/confidence).
N_EFF_MIN_FLOOR: float = 12.0
N_EFF_CONFIDENCE_FULL: float = 20.0
# Cap (R2 item 3) — an unbounded N_eff from a huge weighted sample must not
# keep sharpening the LCB past this ceiling (churn-mitigation ceiling, paired
# with the time-decay weight, never a frequency/count throttle).
N_EFF_CAP: float = 30.0

CONFIDENCE_MIN: float = 0.60
CONFIDENCE_MAX: float = 0.80

VerdictBand = Literal["INSUFFICIENT", "COLD_START", "FULL"]


@dataclass(slots=True, frozen=True)
class GrossEdgeSample:
    """One observation feeding the gross-edge LCB — a value (bps or a v0
    percentile-proxy score; this module is unit-agnostic) plus its close
    timestamp for time-decay weighting."""

    value: float
    closed_ts: int


@dataclass(slots=True, frozen=True)
class GrossLcbResult:
    """Winsorized weighted t-LCB over a gross-edge sample series.

    ``verdict_band`` mirrors R2 item 2's cold-start non-starvation contract:
    INSUFFICIENT (n_eff<12) -> no transition, baseline trading continues;
    COLD_START (12<=n_eff<20) -> upgrade/hold only, no demotion off a thin
    sample; FULL (n_eff>=20) -> full-confidence verdict.
    """

    n_samples: int
    n_eff: float
    confidence: float | None
    mean: float | None
    lcb: float | None
    verdict_band: VerdictBand


def winsorize(values: list[float], *, k: float = WINSOR_MAD_K) -> list[float]:
    """Clip outliers to ``median +/- k*1.4826*MAD`` (unweighted, on raw
    observations — the standard winsorization preprocessing step, applied
    BEFORE any time-decay weighting)."""
    if not values:
        return []
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    if mad == 0.0:
        return list(values)
    bound = k * _MAD_TO_SIGMA * mad
    lo, hi = med - bound, med + bound
    return [min(max(v, lo), hi) for v in values]


def time_decay_weight(age_seconds: float, characteristic_time_seconds: float) -> float:
    """``w = exp(-age/H)`` — churn-mitigation time-weighted window (R2 item 3).

    ``age_seconds`` clamped to >=0 (a future-dated close is treated as
    "now", weight 1.0 — defensive, never a negative age exploding the
    exponent). ``characteristic_time_seconds`` <= 0 degrades to weight 1.0
    for every sample (fail-open — never a silent zero-weight collapse).
    """
    if characteristic_time_seconds <= 0.0:
        return 1.0
    age = max(0.0, age_seconds)
    return math.exp(-age / characteristic_time_seconds)


def effective_sample_size(weights: list[float]) -> float:
    """Kish's effective sample size: ``(sum w)^2 / sum(w^2)``. 0.0 for an
    empty series or an all-zero weight series (never a division by zero)."""
    total = sum(weights)
    sq_total = sum(w * w for w in weights)
    if sq_total <= 0.0:
        return 0.0
    return (total * total) / sq_total


def confidence_for_n_eff(n_eff: float) -> float | None:
    """Linear ramp 60%->80% as ``n_eff`` goes 12->20; ``None`` below the
    floor (no verdict); flat 80% at/above full confidence."""
    if n_eff < N_EFF_MIN_FLOOR:
        return None
    if n_eff >= N_EFF_CONFIDENCE_FULL:
        return CONFIDENCE_MAX
    frac = (n_eff - N_EFF_MIN_FLOOR) / (N_EFF_CONFIDENCE_FULL - N_EFF_MIN_FLOOR)
    return CONFIDENCE_MIN + frac * (CONFIDENCE_MAX - CONFIDENCE_MIN)


def _verdict_band(n_eff: float) -> VerdictBand:
    if n_eff < N_EFF_MIN_FLOOR:
        return "INSUFFICIENT"
    if n_eff < N_EFF_CONFIDENCE_FULL:
        return "COLD_START"
    return "FULL"


def _t_critical_one_sided(confidence: float, df: float) -> float:
    """One-sided Student-t critical value via the Fisher-Cornish expansion
    (see module docstring) — stdlib-only (``statistics.NormalDist``)."""
    z = statistics.NormalDist().inv_cdf(confidence)
    if df <= 0.0:
        return z
    g1 = (z**3 + z) / (4.0 * df)
    g2 = (5.0 * z**5 + 16.0 * z**3 + 3.0 * z) / (96.0 * df**2)
    g3 = (3.0 * z**7 + 19.0 * z**5 + 17.0 * z**3 - 15.0 * z) / (384.0 * df**3)
    return z + g1 + g2 + g3


def weighted_percentile(values: list[float], weights: list[float], pct: float) -> float:
    """Weighted percentile (``pct`` in [0, 1]) via weighted-CDF interpolation.

    Sorts ``(value, weight)`` pairs by value, walks the cumulative weight
    fraction, and returns the first value whose cumulative fraction reaches
    ``pct`` (nearest-rank method — matches the ``statistics.median``-style
    robust-stat idiom already used in this module, no numpy dependency).
    """
    if not values:
        return 0.0
    pairs = sorted(zip(values, weights, strict=True), key=lambda p: p[0])
    total = sum(w for _v, w in pairs)
    if total <= 0.0:
        return statistics.median(values)
    target = pct * total
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= target:
            return v
    return pairs[-1][0]


def compute_gross_lcb(
    samples: list[GrossEdgeSample],
    *,
    now_ts: int,
    characteristic_time_seconds: float,
) -> GrossLcbResult:
    """Winsorized weighted t-LCB over ``samples`` (R2 item 2 + item 3).

    Pipeline: winsorize raw values (unweighted) -> time-decay weight each by
    age -> N_eff (capped at :data:`N_EFF_CAP`) -> confidence ramp -> weighted
    mean/SE -> one-sided t-LCB at ``df = round(n_eff) - 1``.

    ``n_eff < N_EFF_MIN_FLOOR`` -> ``lcb=None`` / ``mean=None`` (no verdict,
    caller must hold baseline behaviour — never treat ``None`` as a
    demotion signal).
    """
    n = len(samples)
    if n == 0:
        return GrossLcbResult(
            n_samples=0, n_eff=0.0, confidence=None, mean=None, lcb=None,
            verdict_band="INSUFFICIENT",
        )

    raw_values = [s.value for s in samples]
    winsorized = winsorize(raw_values)
    weights = [
        time_decay_weight(now_ts - s.closed_ts, characteristic_time_seconds)
        for s in samples
    ]

    n_eff = min(effective_sample_size(weights), N_EFF_CAP)
    confidence = confidence_for_n_eff(n_eff)
    band = _verdict_band(n_eff)

    total_w = sum(weights)
    if confidence is None or total_w <= 0.0:
        return GrossLcbResult(
            n_samples=n, n_eff=n_eff, confidence=confidence, mean=None, lcb=None,
            verdict_band=band,
        )

    mean = sum(w * v for w, v in zip(weights, winsorized, strict=True)) / total_w
    # Standard error of a weighted-mean estimator (design-based / Horvitz-
    # Thompson style): SE = sqrt(sum(w_i^2*(x_i-mean)^2)) / sum(w_i). Pairs
    # naturally with N_eff (SE ~= sigma/sqrt(N_eff) when weights are uniform).
    se = math.sqrt(
        sum((w * (v - mean)) ** 2 for w, v in zip(weights, winsorized, strict=True))
    ) / total_w
    df = max(1.0, round(n_eff) - 1)
    t_crit = _t_critical_one_sided(confidence, df)
    lcb = mean - t_crit * se

    return GrossLcbResult(
        n_samples=n, n_eff=n_eff, confidence=confidence, mean=mean, lcb=lcb,
        verdict_band=band,
    )
