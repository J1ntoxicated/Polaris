"""Pure benchmark statistics — Sharpe, spread, PSR, deflated-Sharpe (no I/O).

Spec: ``.claude/plans/p1_replay_benchmark_harness_2026-05-31.md`` §3-tier
benchmark (Statistical). Every function is a deterministic pure function of its
arguments — no DB, no network, no global state — so the gate is reproducible.

Honest framing (spec §정직 framing): short OOS samples are NOT a pass/fail
calendar gate. PSR / deflated-Sharpe report the *probability* the observed
real-fee-net Sharpe beats a benchmark, deflated for the number of trials the
search explored (anti-overfit). A wide CI means "edge present, CI wide" — not a
pass.

Formulas (spec §3):
  Sharpe          SR = mean(r)/std(r) · √periods           (std = sample, ddof=1)
  sharpe_spread   SR_bot − max(SR_baseline)
  PSR             Φ( (SR − SR*)·√(n−1) / √(1 − γ3·SR + (γ4−1)/4·SR²) )
                  (γ3 = skew, γ4 = kurtosis; SR / SR* are per-period Sharpe)
  Deflated SR     PSR with the trial-inflated benchmark
                  SR* = √Var(SR) · ((1−ε)·Φ⁻¹(1−1/T) + ε·Φ⁻¹(1−1/(T·e)))
                  ε = Euler-Mascheroni; T = trial count.
"""

from __future__ import annotations

import math
from typing import Final

__all__ = [
    "deflated_sharpe",
    "kurtosis",
    "probabilistic_sharpe",
    "sharpe_ratio",
    "sharpe_spread",
    "skewness",
]

# Euler-Mascheroni constant — the ε in the deflated-Sharpe SR* expectation of the
# maximum of T standard-normal draws (Bailey & López de Prado).
_EULER_GAMMA: Final[float] = 0.5772156649015329


def _phi(x: float) -> float:
    """Standard-normal CDF Φ via erf (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi_inv(p: float) -> float:
    """Standard-normal inverse CDF Φ⁻¹ (Acklam's rational approximation).

    Accurate to ~1.1e-9 over the open interval; clamps the input away from the
    {0, 1} singularities so callers never hit a domain error.
    """
    p = min(max(p, 1e-15), 1.0 - 1e-15)
    # Coefficients (Peter Acklam).
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)
    p_low = 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p <= 1.0 - p_low:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
            ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
        )
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )


def sharpe_ratio(returns: list[float], *, periods: float = 1.0) -> float:
    """Sharpe = mean(r)/std(r)·√periods. ``std`` is the sample std (ddof=1).

    ``periods=1`` gives the per-trade Sharpe the engine reports (comparable
    trade-for-trade with the baselines on the shared clock). < 2 samples or zero
    variance -> 0.0 (no usable ratio).
    """
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    if var <= 0.0:
        return 0.0
    return mean / math.sqrt(var) * math.sqrt(periods)


def sharpe_spread(sharpe_bot: float, sharpe_baselines: list[float]) -> float:
    """SR_bot − max(SR_baseline). Empty baselines -> just SR_bot."""
    if not sharpe_baselines:
        return sharpe_bot
    return sharpe_bot - max(sharpe_baselines)


def skewness(returns: list[float]) -> float:
    """Population skewness γ3 (biased, /n). 0.0 for < 3 samples / zero var."""
    n = len(returns)
    if n < 3:
        return 0.0
    mean = sum(returns) / n
    m2 = sum((r - mean) ** 2 for r in returns) / n
    if m2 <= 0.0:
        return 0.0
    m3 = sum((r - mean) ** 3 for r in returns) / n
    return float(m3 / (m2 ** 1.5))


def kurtosis(returns: list[float]) -> float:
    """Population kurtosis γ4 (non-excess, /n). 3.0 (normal) for < 4 / zero var."""
    n = len(returns)
    if n < 4:
        return 3.0
    mean = sum(returns) / n
    m2 = sum((r - mean) ** 2 for r in returns) / n
    if m2 <= 0.0:
        return 3.0
    m4 = sum((r - mean) ** 4 for r in returns) / n
    return m4 / (m2 * m2)


def probabilistic_sharpe(
    *, sr: float, n: int, skew: float, kurt: float, sr_star: float = 0.0
) -> float:
    """PSR = Φ((SR − SR*)·√(n−1)/√(1 − γ3·SR + (γ4−1)/4·SR²)).

    Probability the true Sharpe exceeds the benchmark ``sr_star`` given the
    sample's higher moments. ``n < 2`` -> 0.5 (no spread, no evidence). A
    degenerate / non-positive denominator -> 0.5.
    """
    if n < 2:
        return 0.5
    denom_sq = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom_sq <= 0.0:
        return 0.5
    z = (sr - sr_star) * math.sqrt(n - 1) / math.sqrt(denom_sq)
    return _phi(z)


def expected_max_sharpe(*, sr_variance: float, trials: int) -> float:
    """SR* = √Var(SR)·((1−ε)Φ⁻¹(1−1/T)+ε·Φ⁻¹(1−1/(T·e))).

    The expected maximum Sharpe across ``trials`` independent strategy
    configurations under the null (true SR = 0). ``trials <= 1`` -> 0.0 (a single
    trial has no selection inflation). ``sr_variance`` is the variance of the
    Sharpe estimates across trials (the search's dispersion).
    """
    if trials <= 1 or sr_variance <= 0.0:
        return 0.0
    t = float(trials)
    term1 = (1.0 - _EULER_GAMMA) * _phi_inv(1.0 - 1.0 / t)
    term2 = _EULER_GAMMA * _phi_inv(1.0 - 1.0 / (t * math.e))
    return math.sqrt(sr_variance) * (term1 + term2)


def deflated_sharpe(
    *, sr: float, n: int, skew: float, kurt: float, trials: int, sr_variance: float
) -> float:
    """Deflated Sharpe = PSR evaluated against the trial-inflated benchmark SR*.

    ``trials`` is the number of strategy configurations the search explored;
    ``sr_variance`` is the cross-trial variance of the Sharpe estimates. With
    ``trials <= 1`` SR* = 0 and this is identical to the raw PSR. More trials
    raise SR*, which can only LOWER the deflated PSR (spec invariant).
    """
    sr_star = expected_max_sharpe(sr_variance=sr_variance, trials=trials)
    return probabilistic_sharpe(sr=sr, n=n, skew=skew, kurt=kurt, sr_star=sr_star)
