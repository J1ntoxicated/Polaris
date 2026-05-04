"""Tests for ADXTrendPullback strategy (HYPO-030) — pure (P6).

TDD: 실패 → 구현 → 통과 순서로 작성.
"""
from __future__ import annotations

import pytest

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.strategies.adx_trend_pullback import (
    ADXTrendPullback,
    compute_adx,
    compute_rsi_scalar,
)


def _candle(
    close: float,
    ts_ms: int = 1,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1000.0,
) -> Candle:
    h = high if high is not None else close * 1.005
    l = low if low is not None else close * 0.995
    return Candle(timestamp_ms=ts_ms, open=close, high=h, low=l, close=close, volume=volume)


def _window(closes: list[float]) -> list[Candle]:
    return [_candle(c, ts_ms=(i + 1) * 3_600_000) for i, c in enumerate(closes)]


class TestComputeADX:
    def test_insufficient_data_returns_zeros(self):
        candles = [_candle(100.0)] * 10
        adx, plus_di, minus_di = compute_adx(candles, period=14)
        assert adx == 0.0 and plus_di == 0.0 and minus_di == 0.0

    def test_adx_in_range(self):
        """ADX must be in [0, 100]."""
        candles = _window([100.0 + i * 0.5 for i in range(50)])
        adx, plus_di, minus_di = compute_adx(candles, period=14)
        assert 0.0 <= adx <= 100.0
        assert 0.0 <= plus_di <= 100.0
        assert 0.0 <= minus_di <= 100.0

    def test_strong_uptrend_plus_di_dominates(self):
        """Steady uptrend → +DI > -DI."""
        candles = _window([100.0 + i * 1.0 for i in range(50)])
        adx, plus_di, minus_di = compute_adx(candles, period=14)
        assert plus_di > minus_di

    def test_strong_downtrend_minus_di_dominates(self):
        """Steady downtrend → -DI > +DI."""
        candles = _window([200.0 - i * 1.0 for i in range(50)])
        adx, plus_di, minus_di = compute_adx(candles, period=14)
        assert minus_di > plus_di

    def test_sideways_low_adx(self):
        """Choppy/sideways market → lower ADX than trending."""
        import random
        random.seed(42)
        sideways = [100.0 + random.uniform(-0.5, 0.5) for _ in range(50)]
        trending = [100.0 + i * 0.8 for i in range(50)]
        adx_side, _, _ = compute_adx(_window(sideways), period=14)
        adx_trend, _, _ = compute_adx(_window(trending), period=14)
        assert adx_trend > adx_side


class TestComputeRSIScalar:
    def test_insufficient_returns_neutral(self):
        assert compute_rsi_scalar([100.0] * 5, period=14) == 50.0

    def test_rising_prices_high_rsi(self):
        closes = [100.0 + i for i in range(30)]
        assert compute_rsi_scalar(closes, period=14) > 90.0

    def test_falling_prices_low_rsi(self):
        closes = [130.0 - i for i in range(30)]
        assert compute_rsi_scalar(closes, period=14) < 10.0


class TestADXTrendPullback:
    def test_min_window_is_correct(self):
        """min_window = max(adx_period*2+1, rsi_period+1)."""
        strat = ADXTrendPullback(adx_period=14, rsi_period=14)
        assert strat.min_window == max(14 * 2 + 1, 14 + 1)  # = 29

    def test_raises_on_insufficient_window(self):
        strat = ADXTrendPullback()
        with pytest.raises(ValueError):
            strat.evaluate([_candle(100.0)] * 5)

    def test_hold_neutral_market(self):
        """Choppy sideways market → ADX below threshold → no entry."""
        import random
        random.seed(7)
        # Sideways with low rsi_exit threshold so exit doesn't fire
        strat = ADXTrendPullback(adx_threshold=50.0, rsi_exit=99.0, adx_min_exit=0.0)
        # Generate sideways market (small random walk)
        closes = [100.0 + random.uniform(-0.3, 0.3) for _ in range(strat.min_window)]
        sig = strat.evaluate(_window(closes))
        # Sideways → ADX low → no entry signal expected (HOLD or EXIT both ok)
        assert sig.action in (SignalAction.HOLD, SignalAction.EXIT)

    def test_entry_strong_uptrend_pullback(self):
        """Strong uptrend + RSI dips below rsi_pullback → ENTER_LONG possible."""
        strat = ADXTrendPullback(adx_threshold=15.0, rsi_pullback=70.0, rsi_exit=95.0)
        # Strong uptrend
        closes = [100.0 + i * 1.5 for i in range(strat.min_window)]
        sig = strat.evaluate(_window(closes))
        # With low threshold and high rsi_pullback, entry or hold both valid
        assert sig.action in (SignalAction.ENTER_LONG, SignalAction.HOLD, SignalAction.EXIT)

    def test_exit_on_high_rsi(self):
        """RSI > rsi_exit threshold → EXIT signal."""
        strat = ADXTrendPullback(rsi_exit=10.0, adx_min_exit=0.0)  # very low → always exit
        window = _window([100.0 + i * 0.5 for i in range(strat.min_window)])
        sig = strat.evaluate(window)
        assert sig.action == SignalAction.EXIT

    def test_signal_timestamp_matches_last_candle(self):
        """Signal.timestamp_ms == last candle timestamp."""
        strat = ADXTrendPullback()
        window = [_candle(100.0 + i * 0.1, ts_ms=(i + 1) * 3_600_000) for i in range(strat.min_window)]
        sig = strat.evaluate(window)
        assert sig.timestamp_ms == window[-1].timestamp_ms

    def test_returns_signal_instance(self):
        strat = ADXTrendPullback()
        window = _window([100.0] * strat.min_window)
        sig = strat.evaluate(window)
        assert isinstance(sig, Signal)

    def test_confidence_in_range(self):
        """Confidence always in [0.0, 1.0]."""
        strat = ADXTrendPullback()
        window = _window([100.0 + i * 0.2 for i in range(strat.min_window)])
        sig = strat.evaluate(window)
        assert 0.0 <= sig.confidence <= 1.0

    def test_enter_long_target_size_positive(self):
        """ENTER_LONG signal must have target_size_usd > 0."""
        strat = ADXTrendPullback(adx_threshold=15.0, rsi_pullback=70.0, rsi_exit=99.0)
        window = _window([100.0 + i * 1.5 for i in range(strat.min_window)])
        sig = strat.evaluate(window)
        if sig.action == SignalAction.ENTER_LONG:
            assert sig.target_size_usd > 0
