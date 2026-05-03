"""tests/domain/test_candle.py — Pure P6 + Property-based P7."""
from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from src.domain.candle import Candle


class TestCandleConstruction:
    def test_valid_candle(self) -> None:
        c = Candle(
            timestamp_ms=1_700_000_000_000,
            open=100.0, high=105.0, low=99.0, close=103.0,
            volume=1000.0,
        )
        assert c.open == 100.0
        assert c.is_bullish

    def test_negative_timestamp_raises(self) -> None:
        with pytest.raises(ValueError, match="timestamp_ms"):
            Candle(timestamp_ms=-1, open=1, high=2, low=0.5, close=1.5, volume=1)

    def test_zero_timestamp_raises(self) -> None:
        with pytest.raises(ValueError, match="timestamp_ms"):
            Candle(timestamp_ms=0, open=1, high=2, low=0.5, close=1.5, volume=1)

    def test_negative_volume_raises(self) -> None:
        with pytest.raises(ValueError, match="volume"):
            Candle(timestamp_ms=1, open=1, high=2, low=0.5, close=1.5, volume=-1)

    def test_low_above_open_raises(self) -> None:
        with pytest.raises(ValueError, match="low"):
            Candle(timestamp_ms=1, open=1, high=2, low=1.5, close=1.8, volume=1)

    def test_high_below_close_raises(self) -> None:
        with pytest.raises(ValueError, match="high"):
            Candle(timestamp_ms=1, open=1, high=1.5, low=0.5, close=2.0, volume=1)


class TestCandleProperties:
    def test_hl_range(self) -> None:
        c = Candle(timestamp_ms=1, open=100, high=105, low=99, close=103, volume=1)
        assert c.hl_range == 6.0

    def test_body_bullish(self) -> None:
        c = Candle(timestamp_ms=1, open=100, high=105, low=99, close=103, volume=1)
        assert c.body == 3.0

    def test_body_bearish(self) -> None:
        c = Candle(timestamp_ms=1, open=103, high=105, low=99, close=100, volume=1)
        assert c.body == -3.0

    def test_is_bearish(self) -> None:
        c = Candle(timestamp_ms=1, open=103, high=105, low=99, close=100, volume=1)
        assert c.is_bearish
        assert not c.is_bullish


class TestCandleImmutable:
    def test_frozen(self) -> None:
        c = Candle(timestamp_ms=1, open=100, high=105, low=99, close=103, volume=1)
        with pytest.raises((AttributeError, TypeError)):
            c.open = 200  # type: ignore[misc]


# ─────── Property-based (P7 Hypothesis) ───────

valid_price = st.floats(min_value=0.01, max_value=1e9, allow_nan=False, allow_infinity=False)
valid_volume = st.floats(min_value=0.0, max_value=1e15, allow_nan=False, allow_infinity=False)
valid_timestamp = st.integers(min_value=1, max_value=2**62)


@st.composite
def valid_candle_components(draw):
    """Generate valid OHLC tuple respecting low <= open/close <= high."""
    open_ = draw(valid_price)
    close = draw(valid_price)
    body_min = min(open_, close)
    body_max = max(open_, close)
    low = draw(st.floats(min_value=0.01, max_value=body_min, allow_nan=False))
    high = draw(st.floats(min_value=body_max, max_value=1e9, allow_nan=False))
    return (open_, high, low, close)


class TestCandleInvariants:
    @given(ts=valid_timestamp, ohlc=valid_candle_components(), vol=valid_volume)
    def test_construction_succeeds_for_valid_inputs(self, ts, ohlc, vol) -> None:
        o, h, l, c = ohlc
        candle = Candle(timestamp_ms=ts, open=o, high=h, low=l, close=c, volume=vol)
        assert candle.low <= min(candle.open, candle.close, candle.high)
        assert candle.high >= max(candle.open, candle.close, candle.low)

    @given(ts=valid_timestamp, ohlc=valid_candle_components(), vol=valid_volume)
    def test_hl_range_non_negative(self, ts, ohlc, vol) -> None:
        o, h, l, c = ohlc
        candle = Candle(timestamp_ms=ts, open=o, high=h, low=l, close=c, volume=vol)
        assert candle.hl_range >= 0

    @given(ts=valid_timestamp, ohlc=valid_candle_components(), vol=valid_volume)
    def test_body_sign_matches_bullish(self, ts, ohlc, vol) -> None:
        o, h, l, c = ohlc
        candle = Candle(timestamp_ms=ts, open=o, high=h, low=l, close=c, volume=vol)
        if candle.body > 0:
            assert candle.is_bullish
        elif candle.body < 0:
            assert candle.is_bearish
        else:
            assert not candle.is_bullish and not candle.is_bearish
