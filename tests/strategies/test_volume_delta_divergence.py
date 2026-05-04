"""Tests for VolumeDeltaDivergence strategy (HYPO-025) — pure (P6)."""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction
from src.strategies.volume_delta_divergence import VolumeDeltaDivergence


def _tick(last: float = 100.0, open24: float = 101.0, ts: int = 60_000_000) -> dict:
    return {"last": last, "open24h": open24, "ts": ts}


def _make_trades(
    ts_ms: int, n_buy: int, n_sell: int, price: float = 100.0, size: float = 1.0
) -> list:
    """Generate synthetic trades with given buy/sell counts."""
    trades = []
    for i in range(n_buy):
        trades.append((ts_ms - i * 100, "buy", size, price))
    for i in range(n_sell):
        trades.append((ts_ms - i * 100, "sell", size, price))
    return trades


class TestVolumeDeltaDivergenceBullish:
    def test_bullish_divergence_enter_long(self):
        """Price down + delta positive → ENTER_LONG."""
        strat = VolumeDeltaDivergence(min_trades=5, price_down_threshold=-0.001, min_delta_ratio=0.10)
        # Price dropped from 101 to 100 (-0.99%), buyers still buying (15 buy / 5 sell)
        tick = _tick(last=100.0, open24=101.0, ts=60_000_000)
        trades = _make_trades(60_000_000, n_buy=15, n_sell=5)
        sig = strat.evaluate_delta(tick, trades)
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.confidence > 0.5

    def test_no_signal_when_no_price_drop(self):
        """Price up + delta positive → no divergence → HOLD."""
        strat = VolumeDeltaDivergence(min_trades=5, price_down_threshold=-0.001)
        tick = _tick(last=102.0, open24=100.0, ts=60_000_000)  # price UP
        trades = _make_trades(60_000_000, n_buy=15, n_sell=5)
        sig = strat.evaluate_delta(tick, trades)
        assert sig.action == SignalAction.HOLD

    def test_exit_on_net_selling(self):
        """Delta strongly negative → EXIT."""
        strat = VolumeDeltaDivergence(min_trades=5, min_delta_ratio=0.10)
        tick = _tick(last=100.0, open24=101.0, ts=60_000_000)
        trades = _make_trades(60_000_000, n_buy=3, n_sell=17)  # sell dominant
        sig = strat.evaluate_delta(tick, trades)
        assert sig.action == SignalAction.EXIT

    def test_insufficient_trades_returns_hold(self):
        """n < min_trades → HOLD."""
        strat = VolumeDeltaDivergence(min_trades=10)
        tick = _tick(last=100.0, open24=101.0, ts=60_000_000)
        trades = _make_trades(60_000_000, n_buy=3, n_sell=3)
        sig = strat.evaluate_delta(tick, trades)
        assert sig.action == SignalAction.HOLD
        assert "insufficient" in sig.reason

    def test_trades_outside_window_excluded(self):
        """Trades outside window_sec are filtered out."""
        strat = VolumeDeltaDivergence(min_trades=5, price_down_threshold=-0.001)
        tick = _tick(last=100.0, open24=101.0, ts=120_000_000)
        # Old trades (outside 60s window) + fresh trades (near neutral delta)
        old_trades = [(60_000, "buy", 1.0, 100.0)] * 20  # very old, outside window
        fresh_trades = _make_trades(120_000_000, n_buy=4, n_sell=4)
        sig = strat.evaluate_delta(tick, old_trades + fresh_trades)
        # Fresh trades only — neutral delta → HOLD (not enough imbalance)
        assert sig.action == SignalAction.HOLD

    def test_evaluate_returns_hold(self):
        """Standard evaluate() always HOLD."""
        from src.domain.candle import Candle
        strat = VolumeDeltaDivergence()
        c = Candle(timestamp_ms=1_000, open=100, high=110, low=90, close=105, volume=1000)
        sig = strat.evaluate([c])
        assert sig.action == SignalAction.HOLD
