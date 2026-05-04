"""Tests for CrossExchangeGap strategy (HYPO-024) — pure (P6)."""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction
from src.strategies.cross_exchange_gap import CrossExchangeGap


def _tick(price: float = 50_000.0, ts: int = 1_000_000) -> dict:
    return {"last": price, "bid": price * 0.9999, "ask": price * 1.0001, "ts": ts}


def _binance(price: float, ts_ms: int = 999_990, now_ms: int = 1_000_000) -> dict:
    return {"price": price, "ts_ms": ts_ms, "now_ms": now_ms}


class TestCrossExchangeGapEntry:
    def test_enter_long_when_binance_leads(self):
        """Binance > OKX by >=3 bps → ENTER_LONG."""
        strat = CrossExchangeGap(entry_gap_bps=3.0, stale_ms=5_000)
        # OKX=50000, Binance=50020 → gap=4bps
        sig = strat.evaluate_cross(_tick(50_000), _binance(50_020))
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.confidence > 0.5
        assert sig.target_size_usd > 0

    def test_no_entry_below_threshold(self):
        """Gap < 3bps → HOLD."""
        strat = CrossExchangeGap(entry_gap_bps=3.0, stale_ms=5_000)
        # OKX=50000, Binance=50010 → gap=2bps
        sig = strat.evaluate_cross(_tick(50_000), _binance(50_010))
        assert sig.action == SignalAction.HOLD

    def test_exit_when_okx_above_binance(self):
        """OKX > Binance by exit_gap_bps → EXIT."""
        strat = CrossExchangeGap(exit_gap_bps=-1.0, stale_ms=5_000)
        # Binance=49990, OKX=50000 → gap=-2bps (OKX leads now)
        sig = strat.evaluate_cross(_tick(50_000), _binance(49_990))
        assert sig.action == SignalAction.EXIT

    def test_stale_binance_returns_hold(self):
        """Binance data older than stale_ms → HOLD."""
        strat = CrossExchangeGap(stale_ms=2_000)
        # now_ms=1_000_000, ts_ms=997_000 → age=3s > stale_ms=2s
        sig = strat.evaluate_cross(_tick(50_000), _binance(50_020, ts_ms=997_000, now_ms=1_000_000))
        assert sig.action == SignalAction.HOLD
        assert "stale" in sig.reason

    def test_anomalous_gap_returns_hold(self):
        """Gap > max_gap_bps → HOLD (data anomaly)."""
        strat = CrossExchangeGap(max_gap_bps=100.0, stale_ms=5_000)
        # gap = 200bps → anomaly
        sig = strat.evaluate_cross(_tick(50_000), _binance(51_000))
        assert sig.action == SignalAction.HOLD
        assert "anomaly" in sig.reason

    def test_zero_binance_price_returns_hold(self):
        """Binance price = 0 → HOLD."""
        strat = CrossExchangeGap(stale_ms=5_000)
        sig = strat.evaluate_cross(_tick(50_000), _binance(0.0))
        assert sig.action == SignalAction.HOLD

    def test_evaluate_returns_hold(self):
        """Standard evaluate() always returns HOLD (candle-based not supported)."""
        from src.domain.candle import Candle
        strat = CrossExchangeGap()
        c = Candle(timestamp_ms=1_000, open=100, high=110, low=90, close=105, volume=1000)
        sig = strat.evaluate([c])
        assert sig.action == SignalAction.HOLD
