"""tests/strategies/test_tick_momentum.py — TickMomentum unit tests (Phase 2g Round 13)."""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction
from src.strategies.tick_momentum import (
    DEFAULT_TARGET_SIZE_USD,
    TickMomentum,
)


# ---------------------------------------------------------------------------
# Round 13 regression — DEFAULT_TARGET_SIZE_USD == 300
# ---------------------------------------------------------------------------

class TestRound13SizeUp:
    def test_default_target_size_usd_300_round13(self) -> None:
        """HYPO-010 Round 13: partial size-up ×1.5 (200 → 300). n=70, win 57%."""
        assert DEFAULT_TARGET_SIZE_USD == 300.0

    def test_instance_target_size_usd_300_round13(self) -> None:
        """Instance default matches module constant."""
        s = TickMomentum()
        assert s.target_size_usd == 300.0

    def test_enter_long_signal_carries_300_size(self) -> None:
        """ENTER_LONG signal target_size_usd == 300 (size-up propagates to signal)."""
        s = TickMomentum()
        tick = {
            "ts": 1_700_000_000_000,
            "last": 101.0,
            "bid": 100.9,
            "ask": 101.1,
            "open24h": 96.0,   # +5.2% change → above 1.5% threshold
            "high24h": 101.5,
            "low24h": 95.0,
        }
        # last (101.0) > high24h * 0.99 (100.485) → ENTER_LONG
        sig = s.evaluate_tick(tick)
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.target_size_usd == 300.0


# ---------------------------------------------------------------------------
# Core evaluate_tick logic
# ---------------------------------------------------------------------------

class TestEvaluateTick:
    def _tick(self, last=101.0, open24=96.0, high24=101.5, low24=95.0,
              bid=100.9, ask=101.1, ts=1_700_000_000_000) -> dict:
        return {
            "ts": ts, "last": last, "bid": bid, "ask": ask,
            "open24h": open24, "high24h": high24, "low24h": low24,
        }

    def test_enter_long_momentum(self) -> None:
        s = TickMomentum()
        sig = s.evaluate_tick(self._tick())
        assert sig.action == SignalAction.ENTER_LONG

    def test_exit_panic(self) -> None:
        s = TickMomentum()
        # -5.2% + near low
        tick = self._tick(last=91.0, open24=96.0, high24=97.0, low24=90.5,
                          bid=90.9, ask=91.1)
        sig = s.evaluate_tick(tick)
        assert sig.action == SignalAction.EXIT

    def test_hold_when_spread_too_wide(self) -> None:
        s = TickMomentum(max_spread_bps=10.0)
        # spread = (ask-bid)/last * 10000 = (105-95)/101 * 10000 ≈ 990 bps
        tick = self._tick(bid=95.0, ask=105.0)
        sig = s.evaluate_tick(tick)
        assert sig.action == SignalAction.HOLD
        assert "spread" in sig.reason

    def test_hold_on_zero_fields(self) -> None:
        """Zero price fields → HOLD (invalid tick guard). ts must be positive per Signal domain rule."""
        s = TickMomentum()
        tick = {"ts": 1_700_000_000_000, "last": 0, "bid": 0, "ask": 0,
                "open24h": 0, "high24h": 0, "low24h": 0}
        sig = s.evaluate_tick(tick)
        assert sig.action == SignalAction.HOLD

    def test_evaluate_candle_returns_hold(self) -> None:
        """Standard evaluate(window) → HOLD (tick 전용 전략)."""
        from src.domain.candle import Candle
        s = TickMomentum()
        c = Candle(timestamp_ms=1, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)
        sig = s.evaluate([c])
        assert sig.action == SignalAction.HOLD
