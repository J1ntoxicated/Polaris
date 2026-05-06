"""Tests for src/risk/position_evaluator.py — Phase 23.1."""
from __future__ import annotations

import pytest

from src.risk.position_evaluator import (
    EvaluationInputs,
    HysteresisBands,
    PositionEvaluator,
    PositionState,
    classify_state,
    compute_score,
    fatigue_from_age,
    momentum_from_prices,
)


def _inputs(cont=0.0, mom=0.0, conf=0.0, fatigue=1.0):
    return EvaluationInputs(
        continuation_signal=cont,
        momentum_score=mom,
        confluence_score=conf,
        fatigue_factor=fatigue,
    )


# ─── compute_score ──────────────────────────────────────────────────────────


class TestScore:
    def test_neutral_zero(self):
        assert compute_score(_inputs()) == 0.0

    def test_strong_bullish(self):
        s = compute_score(_inputs(cont=1.0, mom=1.0, conf=1.0))
        # 0.5 + 0.3 + 0.2 = 1.0
        assert abs(s - 1.0) < 1e-9

    def test_strong_bearish(self):
        # confluence is [0,1] not [-1,1] so doesn't go negative
        # cont -1 + mom -1 + conf 0: 0.5*(-1) + 0.3*(-1) + 0.2*0 = -0.8
        s = compute_score(_inputs(cont=-1.0, mom=-1.0, conf=0.0))
        assert s == -0.8

    def test_fatigue_dampens_positive(self):
        # Strong positive but fatigued
        s = compute_score(_inputs(cont=1.0, mom=1.0, conf=1.0, fatigue=0.5))
        # base 1.0, fatigue 0.5 → 0.5
        assert abs(s - 0.5) < 1e-9

    def test_fatigue_clamps_at_minimum(self):
        # Very fatigued (e.g. 0.01 from 100x typical hold)
        s = compute_score(_inputs(cont=1.0, mom=1.0, conf=1.0, fatigue=0.01))
        # max(0.5, 0.01) = 0.5 floor
        assert abs(s - 0.5) < 1e-9

    def test_fatigue_no_effect_on_negative(self):
        # Negative score should stay negative (fatigue only dampens positive)
        s = compute_score(_inputs(cont=-1.0, mom=-1.0, fatigue=0.5))
        assert s == -0.8  # unchanged


# ─── classify_state — hysteresis ────────────────────────────────────────────


class TestClassifyHysteresis:
    def test_initial_warm_zone(self):
        # No previous state, score 0.4 → WARM (between 0.25 and 0.65)
        assert classify_state(0.4, None) == PositionState.WARM

    def test_hot_at_enter(self):
        assert classify_state(0.65, None) == PositionState.HOT
        assert classify_state(0.80, None) == PositionState.HOT

    def test_cold_at_enter(self):
        assert classify_state(0.25, None) == PositionState.COLD
        assert classify_state(0.10, None) == PositionState.COLD

    def test_losing_below_zero(self):
        assert classify_state(-0.01, None) == PositionState.LOSING
        assert classify_state(-0.5, None) == PositionState.LOSING

    def test_hot_stays_hot_in_band(self):
        # Score 0.55 → still HOT if previously HOT
        assert classify_state(0.55, PositionState.HOT) == PositionState.HOT
        # Score 0.50 → exits HOT (below 0.55 hot_exit) → WARM
        assert classify_state(0.50, PositionState.HOT) == PositionState.WARM

    def test_cold_stays_cold_in_band(self):
        # Score 0.30 → still COLD if previously COLD
        assert classify_state(0.30, PositionState.COLD) == PositionState.COLD
        # Score 0.40 → exits COLD (above 0.35 cold_exit) → WARM
        assert classify_state(0.40, PositionState.COLD) == PositionState.WARM

    def test_warm_to_hot_transition(self):
        assert classify_state(0.70, PositionState.WARM) == PositionState.HOT

    def test_warm_to_cold_transition(self):
        assert classify_state(0.20, PositionState.WARM) == PositionState.COLD

    def test_no_boundary_churn(self):
        """Score oscillating around 0.6 doesn't churn HOT/WARM."""
        # Initial: score 0.66 → HOT
        s = classify_state(0.66, None)
        assert s == PositionState.HOT
        # Score drops to 0.58 — still in HOT band [0.55, ...] → stays HOT
        s = classify_state(0.58, s)
        assert s == PositionState.HOT
        # Score 0.62 → still HOT
        s = classify_state(0.62, s)
        assert s == PositionState.HOT
        # Score 0.50 → drops out of hot_exit (0.55) → WARM
        s = classify_state(0.50, s)
        assert s == PositionState.WARM


# ─── PositionEvaluator integration ──────────────────────────────────────────


class TestPositionEvaluator:
    def test_first_eval_no_previous(self):
        e = PositionEvaluator()
        ev = e.evaluate("X", _inputs(cont=1.0, mom=0.5, conf=0.5))
        # 0.5 + 0.15 + 0.1 = 0.75 → HOT
        assert ev.state == PositionState.HOT

    def test_state_persists_across_evals(self):
        e = PositionEvaluator()
        e.evaluate("X", _inputs(cont=1.0, mom=1.0, conf=1.0))  # HOT
        ev2 = e.evaluate("X", _inputs(cont=0.5, mom=0.5, conf=0.5))
        # score = 0.25 + 0.15 + 0.1 = 0.5 — HOT exit threshold 0.55
        # 0.5 < 0.55 → exits HOT → WARM
        assert ev2.state == PositionState.WARM

    def test_reset_clears(self):
        e = PositionEvaluator()
        e.evaluate("X", _inputs(cont=1.0))
        e.reset("X")
        assert "X" not in e._last_state

    def test_forward_ev_proportional_to_score(self):
        e = PositionEvaluator()
        ev = e.evaluate("X", _inputs(cont=1.0, mom=1.0, conf=1.0))
        # score 1.0 → forward_ev 0.01 (1%)
        assert abs(ev.forward_ev_pct - 0.01) < 1e-9


# ─── momentum / fatigue helpers ─────────────────────────────────────────────


class TestMomentum:
    def test_uptrend_positive(self):
        # 100, 100.5, 101 — last above avg
        assert momentum_from_prices([100, 100.5, 101]) > 0

    def test_downtrend_negative(self):
        assert momentum_from_prices([101, 100.5, 100]) < 0

    def test_flat_zero(self):
        assert momentum_from_prices([100, 100, 100]) == 0

    def test_short_series_zero(self):
        assert momentum_from_prices([100]) == 0

    def test_clamped_in_range(self):
        # Extreme spike — should clamp at ±1
        s = momentum_from_prices([100, 100, 200])
        assert s <= 1.0


class TestFatigue:
    def test_fresh_no_fatigue(self):
        assert fatigue_from_age(30, typical_hold_min=60) == 1.0

    def test_old_fatigued(self):
        # 2× typical → 0.5
        assert abs(fatigue_from_age(120, 60) - 0.5) < 1e-9

    def test_very_old_floored(self):
        # 100× typical → 0.01 → floor at 0.1
        assert fatigue_from_age(6000, 60) == 0.1
