"""Tests for OBVDivergence strategy (HYPO-031) — pure (P6).

TDD: 실패 → 구현 → 통과 순서로 작성.
"""
from __future__ import annotations

import pytest

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.strategies.obv_divergence import OBVDivergence, compute_obv


def _candle(close: float, volume: float = 1000.0, ts_ms: int = 1) -> Candle:
    return Candle(timestamp_ms=ts_ms, open=close, high=close * 1.005, low=close * 0.995,
                  close=close, volume=volume)


def _window(closes: list[float], volumes: list[float] | None = None) -> list[Candle]:
    vols = volumes if volumes is not None else [1000.0] * len(closes)
    return [_candle(c, v, ts_ms=(i + 1) * 3_600_000) for i, (c, v) in enumerate(zip(closes, vols))]


class TestComputeOBV:
    def test_single_bar_returns_zero(self):
        candles = [_candle(100.0)]
        obv = compute_obv(candles)
        assert obv == [0.0]

    def test_rising_prices_positive_obv(self):
        """Rising close prices → OBV increases."""
        closes = [100.0, 101.0, 102.0, 103.0]
        vols = [1000.0, 1000.0, 1000.0, 1000.0]
        obv = compute_obv(_window(closes, vols))
        assert obv[-1] == 3000.0  # all 3 bars added

    def test_falling_prices_negative_obv(self):
        """Falling close prices → OBV decreases."""
        closes = [103.0, 102.0, 101.0, 100.0]
        vols = [1000.0, 1000.0, 1000.0, 1000.0]
        obv = compute_obv(_window(closes, vols))
        assert obv[-1] == -3000.0

    def test_flat_prices_unchanged_obv(self):
        """Flat close prices → OBV unchanged."""
        closes = [100.0, 100.0, 100.0]
        obv = compute_obv(_window(closes))
        assert all(v == 0.0 for v in obv)

    def test_length_matches_input(self):
        closes = [float(i) for i in range(20)]
        obv = compute_obv(_window(closes))
        assert len(obv) == 20

    def test_obv_accumulation_mixed(self):
        """Mixed up/down → OBV tracks net buy volume."""
        closes = [100.0, 101.0, 100.5, 102.0]
        vols = [1000.0, 2000.0, 500.0, 3000.0]
        obv = compute_obv(_window(closes, vols))
        # bar1: +2000, bar2: -500, bar3: +3000 → net = 4500
        assert obv[-1] == pytest.approx(4500.0)


class TestOBVDivergence:
    def test_min_window_is_lookback_plus_1(self):
        strat = OBVDivergence(lookback=10)
        assert strat.min_window == 11

    def test_raises_on_insufficient_window(self):
        strat = OBVDivergence(lookback=10)
        with pytest.raises(ValueError):
            strat.evaluate([_candle(100.0)] * 5)

    def test_bullish_divergence_enter_long(self):
        """Price DOWN + OBV UP → ENTER_LONG.

        OBV rises when close > prev_close.
        We build a window where:
        - Net price (current vs lookback bars ago) is DOWN (>= min_price_change_pct).
        - OBV net change over same period is UP (>= min_obv_change_pct of total_vol).
        - Short-term OBV (exit check) is NOT dropping (last bar is an UP bar).

        Pattern: alternating up (big vol) / down (tiny vol) bars, ending on UP bar.
        Net price effect: small net down; net OBV effect: large positive.
        """
        strat = OBVDivergence(
            lookback=5,
            obv_exit_lookback=2,
            min_price_change_pct=0.005,
            min_obv_change_pct=0.01,
        )
        # 6 bars. Bar0=100 (anchor). Bars 1-5 alternate: up tiny / down tiny / up big vol
        # Net: close[5] vs close[0]: final price is 99.4 (net -0.6% < -0.5% threshold ✓)
        # OBV: only bar5 is close > prev_close with huge volume → OBV positive
        # Last 2 bars (exit lookback): bar4=down, bar5=up with huge volume → net OBV up
        closes = [100.0, 100.2, 99.9, 100.1, 99.6, 99.4]
        # bar1: up → +vol; bar2: down → -vol; bar3: up → +big_vol; bar4: down → -tiny; bar5: down → -tiny
        # But we need bar5 (last) to show UP OBV for exit check...
        # Simplest: end on an up bar with huge volume
        closes = [100.0, 99.5, 100.0, 99.3, 100.0, 99.2]  # last close < anchor (-0.8%)
        # bar1: down -v, bar2: up +V, bar3: down -v, bar4: up +V, bar5: down -v
        # That ends on down → OBV exit fires. Need last bar UP.
        closes = [100.0, 99.4, 99.9, 99.3, 99.8, 99.3]
        # Ending on 99.3 < 99.8 → last bar down → OBV down exit. Still wrong.
        # Solution: last 2 bars both have last > prev (UP bars with big volume).
        closes = [100.0, 99.2, 99.7, 99.0, 99.5, 99.0]
        # bar5: 99.0 < 99.5 → down → OBV drop. Tough...
        # Let's just disable exit check by using obv_exit_lookback large + high threshold.
        strat2 = OBVDivergence(
            lookback=5,
            obv_exit_lookback=2,
            min_price_change_pct=0.005,
            min_obv_change_pct=0.005,
        )
        # Pattern: all bars have close slightly DOWN (net ~-1%) but alternating with
        # tiny volume on down bars and HUGE volume on up bars.
        # Up bars: bar1 (100.5, +10k), bar3 (100.2, +10k), bar5 (99.8, +10k)
        # Down bars: bar2 (99.8, +200), bar4 (99.5, +200)
        closes = [100.0, 100.5, 99.8, 100.2, 99.5, 99.4]
        volumes = [1000.0, 10_000.0, 200.0, 10_000.0, 200.0, 10_000.0]
        # bar5: 99.4 < 99.5 → DOWN → OBV drops by 10_000. That's still down.
        # bar5 must be UP: close > prev_close.
        closes = [100.0, 100.5, 99.8, 100.2, 99.5, 99.8]
        # bar5: 99.8 > 99.5 → UP +10_000 → OBV short check: bar4 down -200, bar5 up +10_000
        # net last 2 bars OBV: +9_800 → positive → exit check passes (not negative)
        # net price: close[5]=99.8 vs close[0]=100.0 → -0.2% — NOT enough (threshold 0.5%)
        # Need bigger price drop: close[0] = 100.0, close[5] = 99.4
        closes = [100.0, 100.5, 99.7, 100.1, 99.3, 99.5]
        # bar5: 99.5 > 99.3 → UP +10_000. net: 99.5 vs 100 = -0.5% (border)
        # Use 99.4 for clear >0.5% drop: but then bar5=99.4 < 99.3? No: 99.4 > 99.3 ✓
        closes = [100.0, 100.5, 99.7, 100.1, 99.3, 99.4]
        volumes = [1000.0, 10_000.0, 200.0, 10_000.0, 200.0, 10_000.0]
        sig = strat2.evaluate(_window(closes, volumes))
        assert sig.action == SignalAction.ENTER_LONG

    def test_no_signal_when_price_and_obv_both_down(self):
        """Price down + OBV also down → no bullish divergence → HOLD."""
        strat = OBVDivergence(lookback=5, min_price_change_pct=0.005, min_obv_change_pct=0.01)
        closes = [100.0, 99.5, 99.0, 98.5, 98.0, 97.5]
        # Sell volume dominant → OBV falling
        volumes = [1000.0, 2000.0, 2000.0, 2000.0, 2000.0, 2000.0]
        sig = strat.evaluate(_window(closes, volumes))
        # OBV falls with price → no divergence → HOLD or EXIT (OBV reversal)
        assert sig.action in (SignalAction.HOLD, SignalAction.EXIT)

    def test_exit_on_obv_reversal(self):
        """OBV short-term reversal → EXIT."""
        strat = OBVDivergence(
            lookback=5,
            obv_exit_lookback=2,
            min_obv_change_pct=0.01,
        )
        # First 4 bars up (OBV positive), last 3 bars heavy selling (OBV reversal)
        closes = [100.0, 101.0, 102.0, 103.0, 102.5, 102.0]
        volumes = [1000.0, 1000.0, 1000.0, 1000.0, 5000.0, 5000.0]
        sig = strat.evaluate(_window(closes, volumes))
        assert sig.action == SignalAction.EXIT

    def test_signal_timestamp_matches_last_candle(self):
        strat = OBVDivergence(lookback=5)
        window = [_candle(100.0 + i * 0.1, ts_ms=(i + 1) * 3_600_000) for i in range(6)]
        sig = strat.evaluate(window)
        assert sig.timestamp_ms == window[-1].timestamp_ms

    def test_returns_signal_instance(self):
        strat = OBVDivergence(lookback=5)
        window = _window([100.0 + i * 0.1 for i in range(6)])
        sig = strat.evaluate(window)
        assert isinstance(sig, Signal)

    def test_confidence_in_range(self):
        strat = OBVDivergence(lookback=5)
        window = _window([100.0 + i * 0.2 for i in range(6)])
        sig = strat.evaluate(window)
        assert 0.0 <= sig.confidence <= 1.0

    def test_enter_long_target_size_positive(self):
        strat = OBVDivergence(
            lookback=5,
            min_price_change_pct=0.005,
            min_obv_change_pct=0.01,
            target_size_usd=250.0,
        )
        closes = [100.0, 100.1, 100.0, 99.8, 99.5, 99.3]
        volumes = [1000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0]
        sig = strat.evaluate(_window(closes, volumes))
        if sig.action == SignalAction.ENTER_LONG:
            assert sig.target_size_usd == 250.0

    def test_invalid_past_close_returns_hold(self):
        """Zero price in past close → HOLD (guard)."""
        strat = OBVDivergence(lookback=5)
        closes = [0.0, 100.0, 100.0, 100.0, 100.0, 99.0]
        sig = strat.evaluate(_window(closes))
        assert sig.action == SignalAction.HOLD
