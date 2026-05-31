"""Benchmark statistics TDD — Sharpe, spread, PSR, deflated-Sharpe.

Pure math, no I/O. Hand-computed fixtures + the spec invariant deflated <= PSR
for T > 1 (a non-trivial trial count inflates SR* and can only LOWER the
probability the observed Sharpe beats the inflated benchmark).
"""

from __future__ import annotations

import math
import statistics as _stat

import pytest

from polaris.core.benchmark.statistics import (
    deflated_sharpe,
    probabilistic_sharpe,
    sharpe_ratio,
    sharpe_spread,
)


# --- Sharpe -----------------------------------------------------------------
def test_sharpe_matches_hand_computed() -> None:
    rets = [0.01, -0.02, 0.03, 0.00, 0.02]
    n = len(rets)
    mean = sum(rets) / n
    std = _stat.stdev(rets)  # sample std (ddof=1)
    expected = mean / std * math.sqrt(1.0)  # periods=1 -> per-trade Sharpe
    assert sharpe_ratio(rets, periods=1.0) == pytest.approx(expected)


def test_sharpe_period_scaling() -> None:
    rets = [0.01, -0.02, 0.03, 0.00, 0.02]
    base = sharpe_ratio(rets, periods=1.0)
    scaled = sharpe_ratio(rets, periods=252.0)
    assert scaled == pytest.approx(base * math.sqrt(252.0))


def test_sharpe_zero_variance_is_zero() -> None:
    assert sharpe_ratio([0.01, 0.01, 0.01], periods=1.0) == 0.0


def test_sharpe_too_few_samples_is_zero() -> None:
    assert sharpe_ratio([0.05], periods=1.0) == 0.0
    assert sharpe_ratio([], periods=1.0) == 0.0


# --- spread -----------------------------------------------------------------
def test_sharpe_spread_is_bot_minus_best_baseline() -> None:
    assert sharpe_spread(1.5, [0.4, 0.9, -0.2]) == pytest.approx(1.5 - 0.9)


def test_sharpe_spread_no_baselines_is_bot() -> None:
    assert sharpe_spread(0.7, []) == pytest.approx(0.7)


# --- PSR --------------------------------------------------------------------
def test_psr_at_benchmark_is_half() -> None:
    # SR == SR* -> numerator 0 -> Phi(0) = 0.5 regardless of n / moments.
    psr = probabilistic_sharpe(sr=0.5, n=30, skew=0.0, kurt=3.0, sr_star=0.5)
    assert psr == pytest.approx(0.5, abs=1e-9)


def test_psr_normal_moments_matches_formula() -> None:
    sr, n, skew, kurt, sr_star = 0.8, 50, 0.0, 3.0, 0.0
    denom = math.sqrt(1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr)
    z = (sr - sr_star) * math.sqrt(n - 1) / denom
    expected = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    assert probabilistic_sharpe(sr=sr, n=n, skew=skew, kurt=kurt, sr_star=sr_star) == pytest.approx(expected)


def test_psr_increases_with_higher_sr() -> None:
    lo = probabilistic_sharpe(sr=0.3, n=40, skew=0.0, kurt=3.0, sr_star=0.0)
    hi = probabilistic_sharpe(sr=0.9, n=40, skew=0.0, kurt=3.0, sr_star=0.0)
    assert hi > lo


def test_psr_degenerate_returns_half() -> None:
    # n < 2 -> no spread -> 0.5 (no evidence either way).
    assert probabilistic_sharpe(sr=1.0, n=1, skew=0.0, kurt=3.0, sr_star=0.0) == pytest.approx(0.5)


# --- deflated Sharpe --------------------------------------------------------
def test_deflated_le_psr_for_many_trials() -> None:
    """T > 1 inflates SR* above 0, so deflated PSR can only be <= the raw PSR
    (which uses SR* = 0). The spec's headline invariant."""
    sr, n, skew, kurt = 0.6, 60, 0.1, 3.5
    psr0 = probabilistic_sharpe(sr=sr, n=n, skew=skew, kurt=kurt, sr_star=0.0)
    for trials in (2, 10, 100):
        dsr = deflated_sharpe(
            sr=sr, n=n, skew=skew, kurt=kurt, trials=trials, sr_variance=0.04
        )
        assert dsr <= psr0 + 1e-12


def test_deflated_single_trial_equals_psr() -> None:
    # T == 1 -> SR* expectation collapses to 0 -> identical to raw PSR.
    sr, n, skew, kurt = 0.6, 60, 0.0, 3.0
    psr0 = probabilistic_sharpe(sr=sr, n=n, skew=skew, kurt=kurt, sr_star=0.0)
    dsr = deflated_sharpe(sr=sr, n=n, skew=skew, kurt=kurt, trials=1, sr_variance=0.04)
    assert dsr == pytest.approx(psr0)


def test_deflated_monotone_decreasing_in_trials() -> None:
    common = dict(sr=0.7, n=80, skew=0.0, kurt=3.0, sr_variance=0.05)
    d2 = deflated_sharpe(trials=2, **common)
    d50 = deflated_sharpe(trials=50, **common)
    d500 = deflated_sharpe(trials=500, **common)
    assert d2 >= d50 >= d500
