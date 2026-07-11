"""Offline Platt/PAV calibration fit utilities (frontgate-scan item #4, G5) — TDD.

DEMO/PAPER · behavior-0 · pure functions, 0 dependencies, never imported by
the live sizing/gating pipeline (offline analysis only, no application path).
"""

from __future__ import annotations

import pytest

from polaris.core.learners.calibration_fit import (
    brier_score,
    coverage_report,
    pav_apply,
    pav_fit,
    platt_apply,
    platt_fit,
)

# ---------------------------------------------------------------------------
# Platt scaling
# ---------------------------------------------------------------------------


def test_platt_fit_degenerate_empty_input_returns_identity() -> None:
    assert platt_fit([], []) == (0.0, 0.0)


def test_platt_fit_single_class_returns_identity() -> None:
    # No separating boundary exists with only one class present.
    assert platt_fit([0.1, 0.2, 0.3], [1, 1, 1]) == (0.0, 0.0)


def test_platt_fit_perfectly_separable_pushes_calibrated_probs_apart() -> None:
    scores = [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]
    labels = [0, 0, 0, 1, 1, 1]
    a, b = platt_fit(scores, labels)
    low = platt_apply(0.15, a, b)
    high = platt_apply(0.85, a, b)
    assert low < 0.3
    assert high > 0.7
    assert low < high


def test_platt_apply_output_always_in_unit_interval() -> None:
    for score in (-10.0, -1.0, 0.0, 1.0, 10.0):
        p = platt_apply(score, a=2.0, b=-1.0)
        assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# PAV (pool-adjacent-violators isotonic regression)
# ---------------------------------------------------------------------------


def test_pav_fit_empty_input_returns_no_steps() -> None:
    fit = pav_fit([], [])
    assert fit.steps == ()
    assert pav_apply(0.5, fit) == 0.5


def test_pav_fit_output_is_monotone_non_decreasing() -> None:
    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    # Deliberately non-monotone labels to force pooling.
    labels = [0, 1, 0, 1, 0, 1, 1, 1]
    fit = pav_fit(scores, labels)
    means = [m for _x, m in fit.steps]
    assert means == sorted(means)


def test_pav_fit_perfectly_monotone_labels_reproduces_step_function() -> None:
    scores = [0.1, 0.2, 0.3, 0.4]
    labels = [0, 0, 1, 1]
    fit = pav_fit(scores, labels)
    assert pav_apply(0.15, fit) == pytest.approx(0.0)
    assert pav_apply(0.35, fit) == pytest.approx(1.0)


def test_pav_apply_extrapolates_constant_at_boundaries() -> None:
    scores = [0.3, 0.5, 0.7]
    labels = [0, 1, 1]
    fit = pav_fit(scores, labels)
    below = pav_apply(-5.0, fit)
    above = pav_apply(5.0, fit)
    assert below == fit.steps[0][1]
    assert above == fit.steps[-1][1]


# ---------------------------------------------------------------------------
# Brier score
# ---------------------------------------------------------------------------


def test_brier_score_perfect_predictions_is_zero() -> None:
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)


def test_brier_score_uninformed_half_baseline() -> None:
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)


def test_brier_score_empty_input_is_zero() -> None:
    assert brier_score([], []) == 0.0


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def test_coverage_report_bins_cover_every_pair_exactly_once() -> None:
    predicted = [i / 20.0 for i in range(20)]
    labels = [1 if i % 2 == 0 else 0 for i in range(20)]
    bins = coverage_report(predicted, labels, n_bins=5)
    assert sum(b.n for b in bins) == 20


def test_coverage_report_well_calibrated_predictions_match_win_rate() -> None:
    # Predicted == realized exactly -> each bin's mean_predicted should equal
    # its realized_win_rate.
    predicted = [0.1] * 10 + [0.9] * 10
    labels = [0] * 9 + [1] + [1] * 9 + [0]
    bins = coverage_report(predicted, labels, n_bins=2)
    assert len(bins) == 2
    assert bins[0].mean_predicted == pytest.approx(0.1)
    assert bins[0].realized_win_rate == pytest.approx(0.1)
    assert bins[1].mean_predicted == pytest.approx(0.9)
    assert bins[1].realized_win_rate == pytest.approx(0.9)


def test_coverage_report_empty_input_returns_no_bins() -> None:
    assert coverage_report([], []) == ()
