"""Tests for FundingCarry strategy — HYPO-036 (Liu & Yu 2024).

TDD: RED → GREEN → REFACTOR.
Pure P6 tests — no I/O. All funding values injected.
"""
from __future__ import annotations

import pytest

from src.domain.candle import Candle
from src.domain.signal import SignalAction
from src.strategies.funding_carry import FundingCarry, DEFAULT_ENTRY_THRESHOLD, DEFAULT_EXIT_THRESHOLD

TS = 1_000_000  # dummy timestamp


class TestFundingCarryEntry:
    """Entry logic: funding <= threshold → ENTER_LONG."""

    def test_enter_long_when_funding_at_threshold(self):
        """funding_8h == -0.05% (exactly at threshold) → ENTER_LONG."""
        strat = FundingCarry(entry_threshold=-0.0005)
        sig = strat.evaluate_funding(-0.0005, ts_ms=TS)
        assert sig.action == SignalAction.ENTER_LONG

    def test_enter_long_when_funding_below_threshold(self):
        """funding_8h = -0.10% (below threshold) → ENTER_LONG."""
        strat = FundingCarry(entry_threshold=-0.0005)
        sig = strat.evaluate_funding(-0.0010, ts_ms=TS)
        assert sig.action == SignalAction.ENTER_LONG

    def test_hold_when_funding_neutral(self):
        """funding_8h = +0.01% (above threshold) → HOLD."""
        strat = FundingCarry(entry_threshold=-0.0005)
        sig = strat.evaluate_funding(0.0001, ts_ms=TS)
        assert sig.action == SignalAction.HOLD

    def test_hold_when_funding_slightly_above_threshold(self):
        """funding_8h = -0.04% (above -0.05% threshold) → HOLD."""
        strat = FundingCarry(entry_threshold=-0.0005)
        sig = strat.evaluate_funding(-0.0004, ts_ms=TS)
        assert sig.action == SignalAction.HOLD

    def test_hold_on_none_funding(self):
        """No funding data → HOLD (graceful degradation)."""
        strat = FundingCarry()
        sig = strat.evaluate_funding(None, ts_ms=TS)
        assert sig.action == SignalAction.HOLD
        assert "no_funding_data" in sig.reason

    def test_enter_has_positive_target_size(self):
        strat = FundingCarry(target_size_usd=200.0)
        sig = strat.evaluate_funding(-0.0010, ts_ms=TS)
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.target_size_usd == 200.0

    def test_enter_confidence_scales_with_extremity(self):
        """More extreme funding → higher confidence."""
        strat = FundingCarry(entry_threshold=-0.0005)
        sig_mild = strat.evaluate_funding(-0.0005, ts_ms=TS)   # exactly at threshold
        sig_extreme = strat.evaluate_funding(-0.0020, ts_ms=TS)  # 4x extreme
        assert sig_extreme.confidence >= sig_mild.confidence

    def test_spot_only_never_enter_short(self):
        """Positive funding (shorts paying) — SPOT-only → never ENTER_SHORT."""
        strat = FundingCarry()
        sig = strat.evaluate_funding(0.0020, ts_ms=TS)
        assert sig.action != SignalAction.ENTER_SHORT


class TestFundingCarryExit:
    """Exit logic: funding recovered OR max_hold exceeded."""

    def test_exit_when_funding_recovered(self):
        """In position, funding >= 0% (exit threshold) → EXIT."""
        strat = FundingCarry(exit_threshold=0.0)
        sig = strat.evaluate_funding(0.0, ts_ms=TS, in_position=True, position_age_hours=4.0)
        assert sig.action == SignalAction.EXIT
        assert "funding_recovered" in sig.reason

    def test_exit_when_funding_positive(self):
        """In position, funding = +0.05% → EXIT (above exit threshold 0%)."""
        strat = FundingCarry(exit_threshold=0.0)
        sig = strat.evaluate_funding(0.0005, ts_ms=TS, in_position=True, position_age_hours=6.0)
        assert sig.action == SignalAction.EXIT

    def test_exit_when_max_hold_exceeded(self):
        """In position, max_hold_hours elapsed → EXIT (override funding)."""
        strat = FundingCarry(max_hold_hours=12.0)
        # Funding still negative (not recovered) but max hold reached
        sig = strat.evaluate_funding(-0.0010, ts_ms=TS, in_position=True, position_age_hours=12.0)
        assert sig.action == SignalAction.EXIT
        assert "max_hold" in sig.reason

    def test_hold_in_position_when_funding_not_recovered(self):
        """In position, funding still negative and max hold not reached → HOLD."""
        strat = FundingCarry(exit_threshold=0.0, max_hold_hours=12.0)
        sig = strat.evaluate_funding(-0.0008, ts_ms=TS, in_position=True, position_age_hours=4.0)
        assert sig.action == SignalAction.HOLD
        assert "hold_in_position" in sig.reason

    def test_max_hold_at_exact_boundary_exits(self):
        """position_age_hours == max_hold_hours (exactly) → EXIT."""
        strat = FundingCarry(max_hold_hours=12.0)
        sig = strat.evaluate_funding(-0.0005, ts_ms=TS, in_position=True, position_age_hours=12.0)
        assert sig.action == SignalAction.EXIT

    def test_max_hold_priority_over_funding_recovery(self):
        """Max hold exceeded takes priority (exits first in condition order)."""
        strat = FundingCarry(exit_threshold=0.0, max_hold_hours=12.0)
        sig = strat.evaluate_funding(0.0005, ts_ms=TS, in_position=True, position_age_hours=13.0)
        assert sig.action == SignalAction.EXIT
        assert "max_hold" in sig.reason


class TestFundingCarryEvaluateCandleBased:
    """evaluate(window) is not the primary interface — always returns HOLD."""

    def test_evaluate_returns_hold(self):
        strat = FundingCarry()
        c = Candle(timestamp_ms=TS, open=100.0, high=110.0, low=90.0, close=105.0, volume=1000.0)
        sig = strat.evaluate([c])
        assert sig.action == SignalAction.HOLD
        assert "evaluate_funding" in sig.reason

    def test_evaluate_empty_window_holds(self):
        strat = FundingCarry()
        sig = strat.evaluate([])
        assert sig.action == SignalAction.HOLD


class TestFundingCarryMeta:
    """Strategy metadata."""

    def test_strategy_name(self):
        assert FundingCarry().name == "funding_carry"

    def test_default_thresholds(self):
        strat = FundingCarry()
        assert strat.entry_threshold == DEFAULT_ENTRY_THRESHOLD
        assert strat.exit_threshold == DEFAULT_EXIT_THRESHOLD

    def test_custom_params(self):
        strat = FundingCarry(entry_threshold=-0.001, exit_threshold=0.0002, max_hold_hours=8.0)
        assert strat.entry_threshold == -0.001
        assert strat.exit_threshold == 0.0002
        assert strat.max_hold_hours == 8.0

    def test_timestamp_propagated(self):
        strat = FundingCarry()
        sig = strat.evaluate_funding(-0.0010, ts_ms=TS)
        assert sig.timestamp_ms == TS

    def test_confidence_between_zero_and_one(self):
        """All signals must have confidence in [0, 1]."""
        strat = FundingCarry()
        for funding in [-0.0020, -0.0005, 0.0, 0.0010]:
            sig = strat.evaluate_funding(funding, ts_ms=TS)
            assert 0.0 <= sig.confidence <= 1.0, f"confidence out of range for funding={funding}"
