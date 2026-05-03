"""tests/strategies/test_rsi_mean_reversion.py — Pure RSI strategy."""
from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from src.domain.candle import Candle
from src.domain.signal import SignalAction
from src.strategies.rsi_mean_reversion import (
    DEFAULT_OVERBOUGHT,
    DEFAULT_OVERSOLD,
    DEFAULT_PERIOD,
    RSIMeanReversion,
    compute_rsi,
)


def make_candles(prices: list[float]) -> list[Candle]:
    return [
        Candle(
            timestamp_ms=(i + 1) * 3_600_000,
            open=p, high=p * 1.005, low=p * 0.995, close=p, volume=100,
        )
        for i, p in enumerate(prices)
    ]


class TestRSIComputation:
    def test_too_few_data(self) -> None:
        assert compute_rsi([100, 101]) == 50.0

    def test_all_gains(self) -> None:
        prices = [100 + i for i in range(20)]
        rsi = compute_rsi(prices)
        assert rsi == pytest.approx(100.0)

    def test_all_losses(self) -> None:
        prices = [100 - i for i in range(20)]
        rsi = compute_rsi(prices)
        assert rsi == pytest.approx(0.0)

    def test_alternating(self) -> None:
        prices = [100 + (i % 2) for i in range(20)]
        rsi = compute_rsi(prices)
        # 동일 변화량 → RSI 50 근처
        assert 40 < rsi < 60


class TestRSIMeanReversion:
    def test_too_few_window(self) -> None:
        s = RSIMeanReversion()
        candles = make_candles([100] * 5)
        with pytest.raises(ValueError, match="window"):
            s.evaluate(candles)

    def test_oversold_triggers_long(self) -> None:
        # 큰 하락 → RSI 매우 낮음
        prices = [100 - i for i in range(20)]
        candles = make_candles(prices)
        s = RSIMeanReversion()
        signal = s.evaluate(candles)
        assert signal.action == SignalAction.ENTER_LONG
        assert signal.target_size_usd == 1000.0

    def test_overbought_triggers_exit(self) -> None:
        prices = [100 + i for i in range(20)]
        candles = make_candles(prices)
        s = RSIMeanReversion()
        signal = s.evaluate(candles)
        assert signal.action == SignalAction.EXIT

    def test_neutral_holds(self) -> None:
        # 변동 작은 가격 → RSI ~50
        prices = [100 + (i % 2) * 0.1 for i in range(20)]
        candles = make_candles(prices)
        s = RSIMeanReversion()
        signal = s.evaluate(candles)
        assert signal.action == SignalAction.HOLD


# Property-based (P7)


@st.composite
def candle_window(draw, min_size=20, max_size=100):
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    prices = draw(
        st.lists(
            st.floats(min_value=1.0, max_value=10000.0, allow_nan=False),
            min_size=n, max_size=n,
        )
    )
    return make_candles(prices)


class TestRSIInvariants:
    @given(window=candle_window())
    def test_rsi_in_zero_hundred(self, window) -> None:
        closes = [c.close for c in window]
        rsi = compute_rsi(closes, DEFAULT_PERIOD)
        assert 0.0 <= rsi <= 100.0

    @given(window=candle_window())
    def test_strategy_returns_valid_signal(self, window) -> None:
        s = RSIMeanReversion()
        signal = s.evaluate(window)
        assert signal.timestamp_ms == window[-1].timestamp_ms
        # 모든 entry signal은 size > 0
        if signal.action == SignalAction.ENTER_LONG:
            assert signal.target_size_usd > 0
