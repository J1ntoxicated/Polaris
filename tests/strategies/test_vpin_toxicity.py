"""Tests for VPINToxicity — Volume-Synchronized Probability of Informed Trading.

TDD: RED → GREEN → REFACTOR.
Academic citation: Easley, Lopez de Prado, O'Hara (2012) RFS —
"Flow Toxicity and Liquidity in a High-frequency World".

Pure P6 tests — no I/O. evaluate_vpin(tick, buckets) pure function.
"""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction

# Will fail (RED) until src/strategies/vpin_toxicity.py exists
from src.strategies.vpin_toxicity import VPINToxicity


def _make_tick(price: float = 100.0, ts_ms: int = 1_000_000) -> dict:
    """OKX-style tick dict."""
    return {"last": str(price), "ts": str(ts_ms)}


def _make_bucket(buy_vol: float, sell_vol: float) -> dict:
    """Volume bucket with buy/sell split."""
    return {"buy_vol": buy_vol, "sell_vol": sell_vol}


def _high_toxicity_buckets(n: int = 50, bias: float = 0.8) -> list[dict]:
    """VPIN > 0.7: heavy buy imbalance buckets (buy_vol >> sell_vol)."""
    buckets = []
    for _ in range(n):
        buy = bias * 1000.0
        sell = (1.0 - bias) * 1000.0
        buckets.append(_make_bucket(buy, sell))
    return buckets


def _low_toxicity_buckets(n: int = 50) -> list[dict]:
    """VPIN < 0.3: nearly balanced buckets (low informed trading)."""
    buckets = []
    for _ in range(n):
        buckets.append(_make_bucket(500.0, 500.0))  # perfectly balanced → VPIN ~0
    return buckets


def _make_falling_tick(price: float = 97.0, ts_ms: int = 1_000_000) -> dict:
    """Tick after price drop (for mean-revert signal)."""
    return {"last": str(price), "ts": str(ts_ms)}


class TestVPINBasic:
    """Core VPIN signal logic."""

    def test_high_toxicity_enters_long(self):
        """VPIN > 0.7 (high informed buy flow) → ENTER_LONG (momentum signal)."""
        strategy = VPINToxicity(high_toxicity_threshold=0.7, low_toxicity_threshold=0.3)
        buckets = _high_toxicity_buckets(50, bias=0.85)
        tick = _make_tick(price=100.0)
        signal = strategy.evaluate_vpin(tick, buckets)
        assert signal.action == SignalAction.ENTER_LONG

    def test_balanced_flow_returns_hold(self):
        """Balanced VPIN (≈ 0.0 — equal buy/sell) → HOLD."""
        strategy = VPINToxicity()
        buckets = _low_toxicity_buckets(50)
        tick = _make_tick(price=100.0)
        signal = strategy.evaluate_vpin(tick, buckets)
        assert signal.action == SignalAction.HOLD

    def test_insufficient_buckets_returns_hold(self):
        """Less than min_buckets → HOLD (warmup)."""
        strategy = VPINToxicity(min_buckets=10)
        buckets = _high_toxicity_buckets(5)  # need 10
        tick = _make_tick()
        signal = strategy.evaluate_vpin(tick, buckets)
        assert signal.action == SignalAction.HOLD

    def test_empty_buckets_returns_hold(self):
        """Empty bucket list → HOLD."""
        strategy = VPINToxicity()
        signal = strategy.evaluate_vpin(_make_tick(), [])
        assert signal.action == SignalAction.HOLD

    def test_vpin_formula_pure_calculation(self):
        """VPIN = sum(|buy-sell|) / sum(total) must be computed correctly."""
        strategy = VPINToxicity()
        # 5 buckets: each buy=800, sell=200 → |diff|=600, total=1000 → VPIN = 3000/5000 = 0.6
        buckets = [_make_bucket(800.0, 200.0) for _ in range(5)]
        vpin = strategy.compute_vpin(buckets)
        assert abs(vpin - 0.6) < 1e-9


class TestVPINSignalProperties:
    """Signal metadata."""

    def test_enter_long_has_positive_confidence(self):
        """ENTER_LONG confidence > 0."""
        strategy = VPINToxicity()
        buckets = _high_toxicity_buckets(50, bias=0.90)
        signal = strategy.evaluate_vpin(_make_tick(), buckets)
        assert signal.action == SignalAction.ENTER_LONG
        assert signal.confidence > 0.0

    def test_enter_long_has_positive_target_size(self):
        """ENTER_LONG target_size_usd > 0."""
        strategy = VPINToxicity()
        buckets = _high_toxicity_buckets(50, bias=0.90)
        signal = strategy.evaluate_vpin(_make_tick(), buckets)
        assert signal.action == SignalAction.ENTER_LONG
        assert signal.target_size_usd > 0.0

    def test_signal_timestamp_from_tick(self):
        """Signal timestamp_ms comes from tick ts."""
        strategy = VPINToxicity()
        buckets = _high_toxicity_buckets(50, bias=0.90)
        tick = _make_tick(ts_ms=9_999_000)
        signal = strategy.evaluate_vpin(tick, buckets)
        assert signal.timestamp_ms == 9_999_000

    def test_strategy_name_is_vpin(self):
        """strategy.name == 'vpin_toxicity'."""
        strategy = VPINToxicity()
        assert strategy.name == "vpin_toxicity"

    def test_spot_only_no_short_signal(self):
        """Never returns ENTER_SHORT (SPOT-only)."""
        strategy = VPINToxicity()
        # Both high and low toxicity → no SHORT
        for buckets in [_high_toxicity_buckets(50), _low_toxicity_buckets(50)]:
            signal = strategy.evaluate_vpin(_make_tick(), buckets)
            assert signal.action != SignalAction.ENTER_SHORT


class TestVPINComputeFunction:
    """compute_vpin pure function isolation."""

    def test_compute_vpin_zero_for_balanced(self):
        """Perfectly balanced: VPIN = 0."""
        strategy = VPINToxicity()
        buckets = [_make_bucket(500.0, 500.0) for _ in range(5)]
        assert strategy.compute_vpin(buckets) == 0.0

    def test_compute_vpin_one_for_all_buy(self):
        """All buy volume: VPIN = 1.0."""
        strategy = VPINToxicity()
        buckets = [_make_bucket(1000.0, 0.0) for _ in range(5)]
        assert abs(strategy.compute_vpin(buckets) - 1.0) < 1e-9

    def test_compute_vpin_empty_returns_zero(self):
        """Empty buckets: VPIN = 0.0 (safe default)."""
        strategy = VPINToxicity()
        assert strategy.compute_vpin([]) == 0.0

    def test_compute_vpin_zero_volume_bucket_skipped(self):
        """Zero-volume bucket: should not cause division by zero."""
        strategy = VPINToxicity()
        buckets = [_make_bucket(0.0, 0.0), _make_bucket(800.0, 200.0)]
        # Only non-zero bucket contributes → 600/1000 = 0.6
        vpin = strategy.compute_vpin(buckets)
        assert 0.0 <= vpin <= 1.0  # no crash, valid range
