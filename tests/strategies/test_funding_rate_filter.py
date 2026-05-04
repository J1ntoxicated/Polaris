"""Tests for FundingRateFilter strategy (HYPO-027) — pure (P6)."""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction
from src.strategies.funding_rate_filter import FundingRateFilter


class TestFundingRateFilterMultiplier:
    def test_neutral_on_none(self):
        """None funding rate → neutral multiplier 1.0."""
        strat = FundingRateFilter()
        assert strat.compute_multiplier(None) == 1.0

    def test_squeeze_boost_below_threshold(self):
        """funding_8h <= -0.05% → boost multiplier."""
        strat = FundingRateFilter(squeeze_threshold=-0.0005, boost_multiplier=1.20)
        assert strat.compute_multiplier(-0.001) == 1.20  # -0.1% < -0.05%
        assert strat.compute_multiplier(-0.0005) == 1.20  # exactly at threshold

    def test_block_above_threshold(self):
        """funding_8h >= +0.10% → block (0.0)."""
        strat = FundingRateFilter(block_threshold=0.0010)
        assert strat.compute_multiplier(0.0015) == 0.0  # +0.15% >= +0.10%
        assert strat.compute_multiplier(0.0010) == 0.0  # exactly at threshold

    def test_neutral_in_deadzone(self):
        """Between squeeze and block → neutral."""
        strat = FundingRateFilter(squeeze_threshold=-0.0005, block_threshold=0.0010)
        assert strat.compute_multiplier(0.0) == 1.0
        assert strat.compute_multiplier(0.0003) == 1.0  # +0.03% — in deadzone
        assert strat.compute_multiplier(-0.0003) == 1.0  # -0.03% — in deadzone

    def test_describe_block(self):
        """describe() returns BLOCK string when blocked."""
        strat = FundingRateFilter(block_threshold=0.0010)
        desc = strat.describe(0.0020)
        assert "BLOCK" in desc

    def test_describe_boost(self):
        """describe() returns BOOST string when squeezed."""
        strat = FundingRateFilter(squeeze_threshold=-0.0005, boost_multiplier=1.20)
        desc = strat.describe(-0.0010)
        assert "BOOST" in desc

    def test_describe_neutral(self):
        """describe() returns neutral string in deadzone."""
        strat = FundingRateFilter()
        desc = strat.describe(0.0001)
        assert "neutral" in desc

    def test_evaluate_returns_hold(self):
        """Standard evaluate() always HOLD."""
        from src.domain.candle import Candle
        strat = FundingRateFilter()
        c = Candle(timestamp_ms=1_000, open=100, high=110, low=90, close=105, volume=1000)
        sig = strat.evaluate([c])
        assert sig.action == SignalAction.HOLD
