"""Tests for TickBurst strategy (HYPO-028) — pure (P6)."""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction
from src.strategies.tick_burst import TickBurst


def _tick(price: float = 100.0, ts: int = 5_000_000) -> dict:
    return {
        "last": price,
        "bid": price * 0.9998,
        "ask": price * 1.0002,
        "ts": ts,
    }


class TestTickBurstEntry:
    def test_enter_long_on_burst(self):
        """+0.35% in 5s → ENTER_LONG (above 0.3% threshold with fp safety margin)."""
        strat = TickBurst(burst_threshold=0.003)
        sig = strat.evaluate_tick(_tick(100.35), price_5s_ago=100.0)
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.target_size_usd > 0

    def test_no_entry_below_threshold(self):
        """Burst < 0.3% → HOLD."""
        strat = TickBurst(burst_threshold=0.003)
        # +0.1% only
        sig = strat.evaluate_tick(_tick(100.1), price_5s_ago=100.0)
        assert sig.action == SignalAction.HOLD

    def test_exit_on_reversal(self):
        """Price drops after entry → EXIT."""
        strat = TickBurst(exit_threshold=0.001)
        # -0.2% (reversal)
        sig = strat.evaluate_tick(_tick(99.8), price_5s_ago=100.0)
        assert sig.action == SignalAction.EXIT

    def test_no_history_returns_hold(self):
        """price_5s_ago = None → HOLD."""
        strat = TickBurst()
        sig = strat.evaluate_tick(_tick(100.0), price_5s_ago=None)
        assert sig.action == SignalAction.HOLD
        assert "history" in sig.reason

    def test_bad_tick_guard(self):
        """>5% burst → bad tick guard → HOLD."""
        strat = TickBurst(max_burst_pct=0.05)
        sig = strat.evaluate_tick(_tick(106.0), price_5s_ago=100.0)  # +6%
        assert sig.action == SignalAction.HOLD
        assert "bad tick" in sig.reason

    def test_wide_spread_returns_hold(self):
        """Spread > max_spread_bps → HOLD."""
        strat = TickBurst(burst_threshold=0.003, max_spread_bps=10.0)
        tick = {"last": 100.3, "bid": 99.9, "ask": 100.7, "ts": 5_000_000}
        # spread = (100.7-99.9)/100.3 × 10000 = 79.7 bps > 10 bps
        sig = strat.evaluate_tick(tick, price_5s_ago=100.0)
        assert sig.action == SignalAction.HOLD

    def test_evaluate_returns_hold(self):
        """Standard evaluate() always HOLD."""
        from src.domain.candle import Candle
        strat = TickBurst()
        c = Candle(timestamp_ms=1_000, open=100, high=110, low=90, close=105, volume=1000)
        sig = strat.evaluate([c])
        assert sig.action == SignalAction.HOLD
