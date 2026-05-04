"""Tests for regime_detector — TDD (Phase 2j).

Tests: uptrend / downtrend / crisis (24h -8%) / flat default / insufficient data.
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass

from src.risk.regime_detector import detect_regime


# ── helpers ────────────────────────────────────────────────────────────────

@dataclass
class FakeCandle:
    close: float


def _candles(closes: list[float]) -> list[FakeCandle]:
    return [FakeCandle(close=c) for c in closes]


def _flat_candles(n: int = 50, base: float = 100.0) -> list[FakeCandle]:
    """50 candles all same price (sma20 == sma50 → flat)."""
    return _candles([base] * n)


# ── unit tests ─────────────────────────────────────────────────────────────

class TestUptrend:
    def test_uptrend_detected(self) -> None:
        """sma20 > sma50*1.02 AND last > sma20 → uptrend."""
        # Rising sequence: last 20 prices well above historical 30
        closes = list(range(80, 130))  # 50 values: 80..129
        # last value=129, sma20 ≈ 119.5, sma50 ≈ 104.5 → sma20 > sma50*1.02 ✓
        result = detect_regime(_candles(closes))
        assert result == "uptrend"

    def test_uptrend_requires_last_above_sma20(self) -> None:
        """If last < sma20 even when sma20 > sma50 → not uptrend."""
        # sma20 > sma50 but last price dips below sma20
        closes = list(range(80, 130))
        # Replace last with a dip below sma20 (~119.5)
        closes[-1] = 110.0
        result = detect_regime(_candles(closes))
        assert result != "uptrend"


class TestDowntrend:
    def test_downtrend_detected(self) -> None:
        """sma20 < sma50*0.98 AND last < sma20 → downtrend."""
        # Falling sequence: 129 down to 80
        closes = list(range(129, 79, -1))  # 50 values: 129..80
        # last=80, sma20 ≈ 89.5, sma50 ≈ 104.5 → sma20 < sma50*0.98 ✓
        result = detect_regime(_candles(closes))
        assert result == "downtrend"

    def test_downtrend_requires_last_below_sma20(self) -> None:
        """If last > sma20 even when sma20 < sma50 → not downtrend."""
        closes = list(range(129, 79, -1))
        closes[-1] = 110.0  # spike above sma20 (~89.5)
        result = detect_regime(_candles(closes))
        assert result != "downtrend"


class TestCrisis:
    def test_crisis_24h_drop_8pct(self) -> None:
        """24h drop >= 8% → crisis regardless of SMA."""
        candles = _flat_candles(50, 100.0)
        # Replace last candle with -9% drop
        candles[-1] = FakeCandle(close=91.0)  # -9% from 100
        result = detect_regime(candles)
        assert result == "crisis"

    def test_crisis_exactly_8pct(self) -> None:
        """Exactly -8% → crisis."""
        candles = _flat_candles(50, 100.0)
        candles[-1] = FakeCandle(close=92.0)  # -8%
        result = detect_regime(candles)
        assert result == "crisis"

    def test_no_crisis_7pct_drop(self) -> None:
        """Only -7% drop → not crisis."""
        candles = _flat_candles(50, 100.0)
        candles[-1] = FakeCandle(close=93.0)  # -7%
        result = detect_regime(candles)
        assert result != "crisis"

    def test_crisis_checked_before_sma(self) -> None:
        """Crisis takes priority even in rising trend."""
        # Rising trend but last candle -9% → crisis wins
        closes = list(range(80, 130))
        closes[-1] = closes[-2] * 0.90  # -10% crash
        result = detect_regime(_candles(closes))
        assert result == "crisis"


class TestFlat:
    def test_flat_default(self) -> None:
        """Flat candles → flat regime."""
        result = detect_regime(_flat_candles(50, 100.0))
        assert result == "flat"

    def test_insufficient_data_returns_flat(self) -> None:
        """< 50 candles → default flat."""
        result = detect_regime(_flat_candles(30, 100.0))
        assert result == "flat"

    def test_empty_returns_flat(self) -> None:
        result = detect_regime([])
        assert result == "flat"

    def test_moderate_rise_not_enough_for_uptrend(self) -> None:
        """sma20 only 1% above sma50 (not >= 2%) → flat."""
        # Slight upward slope: not enough to clear 2% threshold
        closes = [100.0] * 30 + [101.0] * 20  # mild slope
        # sma20 ≈ 101, sma50 ≈ 100.4 → sma20/sma50 ≈ 1.006 < 1.02 → flat
        result = detect_regime(_candles(closes))
        assert result == "flat"
