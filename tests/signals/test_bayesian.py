"""Tests for src/signals/bayesian.py — confidence calibration (Phase 27).

TDD: tests written before implementation (P4 mandate).
P7: property-based tests via Hypothesis.

NOTE: bayesian.py is a lite calibration module — NOT a quality gate.
It combines lifetime win_rate × current_score → calibrated confidence.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.signals.bayesian import (
    calibrate_confidence,
    CalibrationInput,
)


# ---------------------------------------------------------------------------
# Unit tests — deterministic
# ---------------------------------------------------------------------------

class TestCalibrationInputValidation:
    def test_valid_input_accepted(self):
        ci = CalibrationInput(win_rate=0.6, composite_score=0.7, n_trades=50)
        assert ci.win_rate == 0.6

    def test_win_rate_zero_valid(self):
        ci = CalibrationInput(win_rate=0.0, composite_score=0.5, n_trades=10)
        assert ci.win_rate == 0.0

    def test_win_rate_one_valid(self):
        ci = CalibrationInput(win_rate=1.0, composite_score=0.5, n_trades=10)
        assert ci.win_rate == 1.0

    def test_win_rate_out_of_range_raises(self):
        with pytest.raises(ValueError):
            CalibrationInput(win_rate=1.1, composite_score=0.5, n_trades=10)

    def test_composite_score_out_of_range_raises(self):
        with pytest.raises(ValueError):
            CalibrationInput(win_rate=0.5, composite_score=-0.1, n_trades=10)

    def test_negative_n_trades_raises(self):
        with pytest.raises(ValueError):
            CalibrationInput(win_rate=0.5, composite_score=0.5, n_trades=-1)

    def test_zero_n_trades_valid(self):
        ci = CalibrationInput(win_rate=0.5, composite_score=0.5, n_trades=0)
        assert ci.n_trades == 0


class TestCalibrateConfidence:
    def test_high_win_rate_high_score_gives_high_confidence(self):
        ci = CalibrationInput(win_rate=0.8, composite_score=0.9, n_trades=100)
        result = calibrate_confidence(ci)
        assert result >= 0.6

    def test_zero_win_rate_gives_low_confidence(self):
        # win_rate=0 but score=0.9 at n=30 → still dominated by prior (score).
        # At very high n_trades, evidence (win_rate=0) pulls confidence toward 0.
        ci_low_n = CalibrationInput(win_rate=0.0, composite_score=0.9, n_trades=30)
        ci_high_n = CalibrationInput(win_rate=0.0, composite_score=0.9, n_trades=500)
        # Higher n_trades → win_rate dominates more → confidence should be lower
        assert calibrate_confidence(ci_high_n) < calibrate_confidence(ci_low_n)
        # At very large n, should be pulled significantly toward win_rate=0
        assert calibrate_confidence(ci_high_n) < 0.3

    def test_zero_composite_score_gives_low_confidence(self):
        # score=0 but win_rate=0.8 at high n_trades → evidence dominates, pulls up.
        # At n=0 (cold start), only prior (score=0) → confidence near 0.
        ci_cold = CalibrationInput(win_rate=0.8, composite_score=0.0, n_trades=0)
        ci_warm = CalibrationInput(win_rate=0.8, composite_score=0.0, n_trades=500)
        result_cold = calibrate_confidence(ci_cold)
        result_warm = calibrate_confidence(ci_warm)
        # Cold start: only score=0 matters → near 0
        assert result_cold < 0.1
        # Warm: win_rate=0.8 lifts it up
        assert result_warm > result_cold

    def test_result_in_unit_interval(self):
        ci = CalibrationInput(win_rate=0.6, composite_score=0.7, n_trades=50)
        result = calibrate_confidence(ci)
        assert 0.0 <= result <= 1.0

    def test_cold_start_n_trades_zero_uses_prior(self):
        # n_trades=0 → no historical evidence → confidence should rely on prior only
        ci = CalibrationInput(win_rate=0.5, composite_score=0.5, n_trades=0)
        result = calibrate_confidence(ci)
        # Should produce a moderate value based on prior, not extreme
        assert 0.0 <= result <= 1.0

    def test_higher_win_rate_gives_higher_confidence_same_score(self):
        ci_low = CalibrationInput(win_rate=0.4, composite_score=0.7, n_trades=50)
        ci_high = CalibrationInput(win_rate=0.8, composite_score=0.7, n_trades=50)
        assert calibrate_confidence(ci_high) > calibrate_confidence(ci_low)

    def test_higher_score_gives_higher_confidence_same_win_rate(self):
        ci_low = CalibrationInput(win_rate=0.6, composite_score=0.2, n_trades=50)
        ci_high = CalibrationInput(win_rate=0.6, composite_score=0.9, n_trades=50)
        assert calibrate_confidence(ci_high) > calibrate_confidence(ci_low)

    def test_more_trades_increases_data_weight(self):
        # Both have same win_rate=0.9 (strong), more n_trades → closer to win_rate effect
        ci_few = CalibrationInput(win_rate=0.9, composite_score=0.8, n_trades=5)
        ci_many = CalibrationInput(win_rate=0.9, composite_score=0.8, n_trades=200)
        # With more evidence, calibrated confidence should be >= few-evidence case
        # (strong win_rate gets more weight with more trades)
        assert calibrate_confidence(ci_many) >= calibrate_confidence(ci_few) - 1e-12

    def test_pure_function_same_input_same_output(self):
        ci = CalibrationInput(win_rate=0.6, composite_score=0.65, n_trades=40)
        assert calibrate_confidence(ci) == calibrate_confidence(ci)

    def test_no_io_side_effects(self):
        # calling multiple times must produce identical results (pure function check)
        ci = CalibrationInput(win_rate=0.55, composite_score=0.6, n_trades=25)
        results = [calibrate_confidence(ci) for _ in range(5)]
        assert len(set(results)) == 1  # all identical


# ---------------------------------------------------------------------------
# Property-based tests (P7 mandate)
# ---------------------------------------------------------------------------

valid_rate = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
valid_score = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
valid_trades = st.integers(min_value=0, max_value=1000)


@given(win_rate=valid_rate, composite_score=valid_score, n_trades=valid_trades)
@settings(max_examples=300)
def test_property_calibrated_always_in_unit_interval(win_rate, composite_score, n_trades):
    """Calibrated confidence must always be in [0, 1]."""
    ci = CalibrationInput(win_rate=win_rate, composite_score=composite_score, n_trades=n_trades)
    result = calibrate_confidence(ci)
    assert 0.0 <= result <= 1.0, f"calibrate={result} out of [0,1] for wr={win_rate} sc={composite_score} n={n_trades}"


@given(win_rate=valid_rate, composite_score=valid_score, n_trades=valid_trades)
@settings(max_examples=200)
def test_property_deterministic(win_rate, composite_score, n_trades):
    """Pure function: same input always returns same output."""
    ci = CalibrationInput(win_rate=win_rate, composite_score=composite_score, n_trades=n_trades)
    assert calibrate_confidence(ci) == calibrate_confidence(ci)


@given(
    win_rate=st.floats(min_value=0.0, max_value=0.9, allow_nan=False),
    composite_score=valid_score,
    n_trades=valid_trades,
    delta=st.floats(min_value=0.01, max_value=0.1, allow_nan=False),
)
@settings(max_examples=150)
def test_property_monotone_in_win_rate(win_rate, composite_score, n_trades, delta):
    """Increasing win_rate alone must not decrease calibrated confidence."""
    hi_wr = min(1.0, win_rate + delta)
    ci_lo = CalibrationInput(win_rate=win_rate, composite_score=composite_score, n_trades=n_trades)
    ci_hi = CalibrationInput(win_rate=hi_wr, composite_score=composite_score, n_trades=n_trades)
    assert calibrate_confidence(ci_hi) >= calibrate_confidence(ci_lo) - 1e-12


@given(
    win_rate=valid_rate,
    composite_score=st.floats(min_value=0.0, max_value=0.9, allow_nan=False),
    n_trades=valid_trades,
    delta=st.floats(min_value=0.01, max_value=0.1, allow_nan=False),
)
@settings(max_examples=150)
def test_property_monotone_in_score(win_rate, composite_score, n_trades, delta):
    """Increasing composite_score alone must not decrease calibrated confidence."""
    hi_sc = min(1.0, composite_score + delta)
    ci_lo = CalibrationInput(win_rate=win_rate, composite_score=composite_score, n_trades=n_trades)
    ci_hi = CalibrationInput(win_rate=win_rate, composite_score=hi_sc, n_trades=n_trades)
    assert calibrate_confidence(ci_hi) >= calibrate_confidence(ci_lo) - 1e-12
