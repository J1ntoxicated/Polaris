"""Tests for src/risk/regime_activation.py — regime × strategy matrix."""
from __future__ import annotations

import pytest

from src.risk.regime_activation import (
    ALL_REGIMES,
    REGIME_ACTIVATION,
    block_reason,
    is_strategy_active,
)


class TestActivationDefaults:
    def test_unknown_strategy_allowed_everywhere(self):
        for r in ("uptrend", "downtrend", "flat", "crisis"):
            assert is_strategy_active("unknown_strategy", r)

    def test_empty_set_treated_as_all(self):
        # Defensive — if a strategy is in matrix with empty set, allow all
        REGIME_ACTIVATION["__test_empty"] = frozenset()
        try:
            for r in ALL_REGIMES:
                assert is_strategy_active("__test_empty", r)
        finally:
            del REGIME_ACTIVATION["__test_empty"]


class TestTSMOM:
    """TSMOM = trend continuation. Block in flat/downtrend."""

    def test_uptrend_active(self):
        assert is_strategy_active("tsmom", "uptrend")

    def test_crisis_active(self):
        # Crisis allowed (mean-revert spike rebound captured by 1d trend)
        assert is_strategy_active("tsmom", "crisis")

    def test_flat_blocked(self):
        # INSIGHT-035: TSMOM 6 simultaneous positions in flat → fee bleed
        assert not is_strategy_active("tsmom", "flat")

    def test_downtrend_blocked(self):
        assert not is_strategy_active("tsmom", "downtrend")


class TestGridBot:
    """Grid bot = range-bound. Block in strong uptrend."""

    def test_flat_active(self):
        assert is_strategy_active("grid_bot", "flat")

    def test_downtrend_active(self):
        # Grid still profitable on dead-cat bounces in downtrend
        assert is_strategy_active("grid_bot", "downtrend")

    def test_uptrend_blocked(self):
        assert not is_strategy_active("grid_bot", "uptrend")

    def test_crisis_blocked(self):
        # Crisis = high vol, breakout buffer would constantly hit
        assert not is_strategy_active("grid_bot", "crisis")


class TestNFIDipBuy:
    """NFI = mean-revert dip buy. Block in strong uptrend."""

    def test_flat_active(self):
        assert is_strategy_active("nfi_dipbuy", "flat")

    def test_downtrend_active(self):
        # True dips require bear context
        assert is_strategy_active("nfi_dipbuy", "downtrend")

    def test_crisis_active(self):
        assert is_strategy_active("nfi_dipbuy", "crisis")

    def test_uptrend_blocked(self):
        # Uptrend = no real dips (false dip-buy signals)
        assert not is_strategy_active("nfi_dipbuy", "uptrend")


class TestLiqCascade:
    def test_crisis_active(self):
        assert is_strategy_active("liquidation_cascade", "crisis")

    def test_downtrend_active(self):
        assert is_strategy_active("liquidation_cascade", "downtrend")

    def test_flat_blocked(self):
        assert not is_strategy_active("liquidation_cascade", "flat")

    def test_uptrend_blocked(self):
        assert not is_strategy_active("liquidation_cascade", "uptrend")


class TestFundingStrategies:
    """Funding-driven strategies — price regime independent, allow all."""

    @pytest.mark.parametrize("regime", ["uptrend", "downtrend", "flat", "crisis"])
    def test_funding_carry_all_regimes(self, regime):
        assert is_strategy_active("funding_carry", regime)

    @pytest.mark.parametrize("regime", ["uptrend", "downtrend", "flat", "crisis"])
    def test_funding_filter_all_regimes(self, regime):
        assert is_strategy_active("funding_rate_filter", regime)


class TestBlockReason:
    def test_active_returns_empty(self):
        assert block_reason("tsmom", "uptrend") == ""

    def test_blocked_returns_reason(self):
        msg = block_reason("tsmom", "flat")
        assert "tsmom" in msg
        assert "flat" in msg
        assert "uptrend" in msg  # allowed list mentioned

    def test_unknown_strategy_returns_empty(self):
        assert block_reason("unknown_x", "flat") == ""
