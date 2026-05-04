"""tests/strategies/test_tick_momentum.py — TickMomentum unit tests (Phase 2g Round 14)."""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction
from src.strategies.tick_momentum import (
    DEFAULT_TARGET_SIZE_USD,
    TickMomentum,
)


# ---------------------------------------------------------------------------
# Round 14 regression — DEFAULT_TARGET_SIZE_USD == 200 (intent-code 정합 복원)
# ---------------------------------------------------------------------------

class TestRound14TargetSize:
    def test_default_target_size_usd_200_round14(self) -> None:
        """HYPO-010 Round 14: target_size_usd 200 복원 — max_position_pct 0.04 × $5000 = $200 cap 정합.

        Round 13의 300 override는 silent cap bug (실제 executed size = $200).
        Round 14: intent-code 정합성 회복.
        """
        assert DEFAULT_TARGET_SIZE_USD == 200.0

    def test_instance_target_size_usd_200_round14(self) -> None:
        """Instance default matches module constant (200.0)."""
        s = TickMomentum()
        assert s.target_size_usd == 200.0

    def test_enter_long_signal_carries_200_size(self) -> None:
        """ENTER_LONG signal target_size_usd == 200 (Round 14 정합 검증)."""
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
        assert sig.target_size_usd == 200.0


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
