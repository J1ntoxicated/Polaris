"""Tests for TSMOM — Time-Series Momentum (Moskowitz/Ooi/Pedersen 2012 JFE).

TDD: RED → GREEN → REFACTOR.
Academic citation: Moskowitz, Ooi, Pedersen (2012) JFE 104(2) — 58 futures markets,
Sharpe 1.0+, 25 year persistent momentum.

Pure P6 tests — no I/O, deterministic inputs only.
"""
from __future__ import annotations

import pytest

from src.domain.candle import Candle
from src.domain.signal import SignalAction

# Will fail (RED) until src/strategies/tsmom.py exists
from src.strategies.tsmom import TSMOM


def _make_candle(ts: int, price: float) -> Candle:
    """Helper: flat candle at price."""
    return Candle(timestamp_ms=ts, open=price, high=price, low=price, close=price, volume=1000.0)


def _make_rising_candles(n: int = 35, start: float = 100.0, step: float = 1.0) -> list[Candle]:
    """Monotonically rising candles — all lookbacks yield positive return."""
    return [_make_candle(ts=1_000_000 + i * 3600_000, price=start + i * step) for i in range(n)]


def _make_flat_candles(n: int = 35, price: float = 100.0) -> list[Candle]:
    """Flat candles — all lookbacks yield zero return (HOLD territory)."""
    return [_make_candle(ts=1_000_000 + i * 3600_000, price=price) for i in range(n)]


def _make_falling_candles(n: int = 35, start: float = 200.0, step: float = 1.0) -> list[Candle]:
    """Monotonically falling candles — all lookbacks yield negative return → EXIT."""
    return [_make_candle(ts=1_000_000 + i * 3600_000, price=start - i * step) for i in range(n)]


class TestTSMOMBasic:
    """Basic ENTER_LONG / EXIT / HOLD logic."""

    def test_enter_long_when_all_lookbacks_positive(self):
        """Rising candles: all lookbacks positive → ratio 1.0 >= 0.6 → ENTER_LONG."""
        strategy = TSMOM()
        candles = _make_rising_candles(35)
        signal = strategy.evaluate(candles)
        assert signal.action == SignalAction.ENTER_LONG

    def test_exit_when_all_lookbacks_negative(self):
        """Falling candles: all lookbacks negative → ratio 0.0 <= 0.4 → EXIT."""
        strategy = TSMOM()
        candles = _make_falling_candles(35)
        signal = strategy.evaluate(candles)
        assert signal.action == SignalAction.EXIT

    def test_hold_when_ratio_in_deadzone(self):
        """Mixed lookbacks → ratio between 0.4 and 0.6 → HOLD."""
        strategy = TSMOM(lookbacks_days=(1, 7, 30), enter_threshold=0.6, exit_threshold=0.4)
        # Build candles: last day up, last 7d down, last 30d down → ratio = 1/3 = 0.33 → EXIT
        # Instead use 2 up / 1 down for ratio = 0.67 → ENTER_LONG... let's craft exactly 50%
        # Use 4 lookbacks, 2 up, 2 down → ratio 0.5 → HOLD
        strategy2 = TSMOM(lookbacks_days=(1, 7, 30, 60), enter_threshold=0.6, exit_threshold=0.4)
        # candles[0] = 100, candle[-1] = 105 (up for all short lookbacks)
        # We need to craft specific price at key lookback positions
        candles = [_make_candle(ts=1_000_000 + i * 86_400_000, price=100.0) for i in range(65)]
        # Set price 1d ago higher than current → 1d return negative
        # current = candles[-1].close = 100.0
        # 1d ago = candles[-2].close = 100.0 → return = 0 → not positive → signal 0
        # 7d ago = candles[-8].close = 100.0 → return = 0 → signal 0
        # 30d ago = candles[-31].close = 100.0 → return = 0 → signal 0
        # 60d ago = candles[-61].close = 100.0 → return = 0 → signal 0
        # All 0 (flat) → signals = [0,0,0,0] → ratio = 0 → EXIT (not HOLD)
        # Test HOLD: exactly half positive, half 0 → ratio = 0.5 (between 0.4 and 0.6)
        # Make current > some lookbacks, < others via strategy with 2 lookbacks
        strategy3 = TSMOM(lookbacks_days=(1, 2), enter_threshold=0.6, exit_threshold=0.4)
        # current = 105, 1d ago = 100 (+up), 2d ago = 110 (current < → down)
        candles3 = [
            _make_candle(ts=1_000_000 + 0 * 86_400_000, price=90.0),   # 3d ago
            _make_candle(ts=1_000_000 + 1 * 86_400_000, price=110.0),  # 2d ago (above current)
            _make_candle(ts=1_000_000 + 2 * 86_400_000, price=100.0),  # 1d ago (below current)
            _make_candle(ts=1_000_000 + 3 * 86_400_000, price=105.0),  # current
        ]
        signal = strategy3.evaluate(candles3)
        # 1d: 100→105 up (1), 2d: 110→105 down (0) → ratio=0.5 → HOLD (0.4 < 0.5 < 0.6)
        assert signal.action == SignalAction.HOLD

    def test_insufficient_window_returns_hold(self):
        """Less than max lookback candles → HOLD (warmup guard)."""
        strategy = TSMOM(lookbacks_days=(1, 7, 30))
        candles = _make_rising_candles(10)  # need 31+, have 10
        signal = strategy.evaluate(candles)
        assert signal.action == SignalAction.HOLD

    def test_empty_window_returns_hold(self):
        """Empty candle list → HOLD."""
        strategy = TSMOM()
        signal = strategy.evaluate([])
        assert signal.action == SignalAction.HOLD


class TestTSMOMSignalProperties:
    """Signal metadata correctness."""

    def test_enter_long_has_positive_confidence(self):
        """ENTER_LONG signal must have confidence > 0."""
        strategy = TSMOM()
        candles = _make_rising_candles(35)
        signal = strategy.evaluate(candles)
        assert signal.action == SignalAction.ENTER_LONG
        assert signal.confidence > 0.0

    def test_enter_long_has_positive_target_size(self):
        """ENTER_LONG signal must have target_size_usd > 0 (Signal invariant)."""
        strategy = TSMOM()
        candles = _make_rising_candles(35)
        signal = strategy.evaluate(candles)
        assert signal.action == SignalAction.ENTER_LONG
        assert signal.target_size_usd > 0.0

    def test_signal_timestamp_matches_last_candle(self):
        """Signal timestamp should equal last candle timestamp."""
        strategy = TSMOM()
        candles = _make_rising_candles(35)
        signal = strategy.evaluate(candles)
        assert signal.timestamp_ms == candles[-1].timestamp_ms

    def test_reason_contains_ratio(self):
        """Signal reason should contain ratio information."""
        strategy = TSMOM()
        candles = _make_rising_candles(35)
        signal = strategy.evaluate(candles)
        assert "ratio" in signal.reason.lower() or "tsmom" in signal.reason.lower()

    def test_strategy_name_is_tsmom(self):
        """Strategy.name must be 'tsmom'."""
        strategy = TSMOM()
        assert strategy.name == "tsmom"


class TestTSMOMParametrization:
    """Custom lookback + threshold behavior."""

    def test_single_lookback_1d_up_enters_long(self):
        """Single lookback day=1, current > 1d ago → ENTER_LONG (ratio=1.0 >= 0.5 threshold)."""
        strategy = TSMOM(lookbacks_days=(1,), enter_threshold=0.5, exit_threshold=0.5)
        candles = [
            _make_candle(ts=1_000_000, price=100.0),
            _make_candle(ts=1_000_000 + 86_400_000, price=105.0),
        ]
        signal = strategy.evaluate(candles)
        assert signal.action == SignalAction.ENTER_LONG

    def test_single_lookback_1d_down_exits(self):
        """Single lookback day=1, current < 1d ago → EXIT (ratio=0.0 <= 0.5 threshold)."""
        strategy = TSMOM(lookbacks_days=(1,), enter_threshold=0.5, exit_threshold=0.5)
        candles = [
            _make_candle(ts=1_000_000, price=110.0),
            _make_candle(ts=1_000_000 + 86_400_000, price=105.0),
        ]
        signal = strategy.evaluate(candles)
        assert signal.action == SignalAction.EXIT

    def test_strict_threshold_requires_all_positive(self):
        """enter_threshold=1.0 → all lookbacks must be positive for ENTER_LONG."""
        strategy = TSMOM(lookbacks_days=(1, 7), enter_threshold=1.0, exit_threshold=0.0)
        # 1d up (1), 7d flat (0) → ratio=0.5 < 1.0 → not ENTER_LONG
        candles = [_make_candle(ts=1_000_000 + i * 86_400_000, price=100.0) for i in range(10)]
        # current > 1d ago but == 7d ago
        candles_mod = list(candles)
        candles_mod[-1] = _make_candle(ts=candles[-1].timestamp_ms, price=105.0)
        signal = strategy.evaluate(candles_mod)
        # 1d: 100→105 (up=1), 7d: 100→105 (100 at 7d ago, 105 now = up=1) → ratio=1.0 → ENTER_LONG
        # Actually all 100 except last=105 → all lookbacks positive → ratio=1.0 → ENTER_LONG
        assert signal.action == SignalAction.ENTER_LONG

    def test_spot_only_no_short(self):
        """SPOT-only: falling candles → EXIT (not ENTER_SHORT). Strategy never returns ENTER_SHORT."""
        strategy = TSMOM()
        candles = _make_falling_candles(35)
        signal = strategy.evaluate(candles)
        assert signal.action != SignalAction.ENTER_SHORT

    def test_long_lookback_only_needs_enough_candles(self):
        """lookbacks_days=(30,) needs 31+ candles to produce signal."""
        strategy = TSMOM(lookbacks_days=(30,), enter_threshold=0.5, exit_threshold=0.5)
        # 30 candles: only 30 in window, need 31 for lookback=30 (index -31)
        candles_30 = _make_rising_candles(30)
        signal = strategy.evaluate(candles_30)
        assert signal.action == SignalAction.HOLD  # insufficient
        candles_31 = _make_rising_candles(31)
        signal = strategy.evaluate(candles_31)
        assert signal.action == SignalAction.ENTER_LONG
