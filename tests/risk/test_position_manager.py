"""Tests for src/risk/position_manager.py — real-time exit monitor."""
from __future__ import annotations

import pytest

from src.exec.exit_strategies import (
    PartialTP,
    SignalReversal,
    StopLoss,
    TakeProfit,
    TimeBasedHold,
    TrailingStop,
)
from src.risk.portfolio_manager import PortfolioManager
from src.risk.position_manager import PositionManager


@pytest.fixture
def portfolio():
    return PortfolioManager(starting_cash_usd=5000.0)


@pytest.fixture
def manager(portfolio):
    return PositionManager(portfolio)


# ─── No exit ────────────────────────────────────────────────────────────────


class TestNoExit:
    def test_empty_portfolio_no_events(self, manager):
        events = manager.check_exits(current_prices={}, ts_ms=1)
        assert events == []

    def test_price_in_safe_range_no_exit(self, portfolio, manager):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1,
            (TakeProfit(0.006), StopLoss(0.0035)),
        )
        events = manager.check_exits({"BTC-USDT": 80100}, ts_ms=2)
        assert events == []
        # Position still open
        assert portfolio.n_open_contributions == 1


# ─── TakeProfit fire ────────────────────────────────────────────────────────


class TestTakeProfit:
    def test_tp_fires(self, portfolio, manager):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1,
            (TakeProfit(0.006), StopLoss(0.0035)),
        )
        events = manager.check_exits({"BTC-USDT": 80800}, ts_ms=2)
        assert len(events) == 1
        assert events[0].ticker == "BTC-USDT"
        assert events[0].strategy_name == "vb"
        assert "tp_hit" in events[0].exit_reason
        assert events[0].fraction == 1.0
        # Portfolio: closed, position removed
        assert portfolio.n_open_contributions == 0


class TestStopLoss:
    def test_sl_fires(self, portfolio, manager):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1,
            (TakeProfit(0.006), StopLoss(0.0035)),
        )
        events = manager.check_exits({"BTC-USDT": 79600}, ts_ms=2)
        assert len(events) == 1
        assert "sl_hit" in events[0].exit_reason


# ─── Time-based exit ────────────────────────────────────────────────────────


class TestTimeBased:
    def test_max_hold_fires(self, portfolio, manager):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1_000_000,
            (TimeBasedHold(1.0),),  # 1 hour
        )
        # After 1.5h
        events = manager.check_exits(
            {"BTC-USDT": 80000}, ts_ms=1_000_000 + int(1.5 * 3_600_000),
        )
        assert len(events) == 1
        assert "max_hold" in events[0].exit_reason


# ─── Trailing stop ──────────────────────────────────────────────────────────


class TestTrailing:
    def test_trail_after_high_water(self, portfolio, manager):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1,
            (TrailingStop(activation_pct=0.005, trail_pct=0.003),),
        )
        # Tick 1: rise to 80800 (+1%) — above activation, no exit
        events = manager.check_exits({"BTC-USDT": 80800}, ts_ms=2)
        assert events == []
        # Tick 2: fall to 80470 (drawdown ≈ 0.41% from 80800) — exit
        events = manager.check_exits({"BTC-USDT": 80470}, ts_ms=3)
        assert len(events) == 1
        assert "trail_stop" in events[0].exit_reason


# ─── PartialTP — multi-level ─────────────────────────────────────────────────


class TestPartialTP:
    def test_partial_tp_first_level(self, portfolio, manager):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1,
            (PartialTP(levels=((0.005, 0.5), (0.010, 0.5))),),
        )
        # +0.6% reached → fire 0.5% level (50% close)
        events = manager.check_exits({"BTC-USDT": 80500}, ts_ms=2)
        assert len(events) == 1
        assert events[0].fraction == 0.5
        # Position still open with 50% remaining
        assert portfolio.n_open_contributions == 1
        pos = portfolio.get_position("BTC-USDT")
        assert pos.contributions[0].size_usd == 50

    def test_partial_tp_second_level_after_first_fired(self, portfolio, manager):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1,
            (PartialTP(levels=((0.005, 0.5), (0.010, 0.5))),),
        )
        # First tick: +0.6% → 0.5% level fires
        manager.check_exits({"BTC-USDT": 80500}, ts_ms=2)
        # Second tick: +1.1% → 1% level fires (0.5% should NOT re-fire)
        events = manager.check_exits({"BTC-USDT": 80800}, ts_ms=3)
        assert len(events) == 1
        # Now 1% level fired (0.5 fraction of remaining 50 = 25 closed)
        assert events[0].fraction == 0.5
        # 25 still open after 2 partial fires (50→25→25)
        pos = portfolio.get_position("BTC-USDT")
        assert pos is not None
        assert abs(pos.contributions[0].size_usd - 25) < 0.01


# ─── Multi-strategy independent exits ───────────────────────────────────────


class TestMultiStrategyIndependentExits:
    """Critical scenario — 2 strategies on same ticker exit independently."""

    def test_scalp_exits_swing_remains(self, portfolio, manager):
        # Scalp at 80000, +0.6% TP
        c_scalp = portfolio.process_entry(
            "BTC-USDT", "vb", "HYPO-008", 100, 80000, 1,
            (TakeProfit(0.006), StopLoss(0.0035)),
        )
        # Swing at 80050, +5% TP
        c_swing = portfolio.process_entry(
            "BTC-USDT", "nfi", "HYPO-NFI-001", 100, 80050, 2,
            (TakeProfit(0.05), StopLoss(0.02)),
        )
        # Tick: 80800 (scalp +1%, swing +0.94%)
        events = manager.check_exits({"BTC-USDT": 80800}, ts_ms=10)
        # Scalp's TP fires, swing still in HOLD range
        assert len(events) == 1
        assert events[0].strategy_name == "vb"
        # Swing still open
        pos = portfolio.get_position("BTC-USDT")
        assert pos.n_open == 1
        assert pos.contributions[0].strategy_name == "nfi"

    def test_both_exit_at_different_thresholds(self, portfolio, manager):
        portfolio.process_entry(
            "BTC-USDT", "vb", "HYPO-008", 100, 80000, 1,
            (TakeProfit(0.006),),
        )
        portfolio.process_entry(
            "BTC-USDT", "nfi", "HYPO-NFI-001", 100, 80000, 2,
            (TakeProfit(0.012),),  # +1.2%
        )
        # Tick at +1% — only scalp exits
        events = manager.check_exits({"BTC-USDT": 80800}, ts_ms=10)
        assert len(events) == 1
        # Tick at +1.5% — swing also exits
        events2 = manager.check_exits({"BTC-USDT": 81200}, ts_ms=20)
        assert len(events2) == 1
        # Both closed
        assert portfolio.n_open_contributions == 0


# ─── SignalReversal exit ────────────────────────────────────────────────────


class TestSignalReversal:
    def test_signal_exit_fires(self, portfolio, manager):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1,
            (SignalReversal("vb"),),
        )
        events = manager.check_exits(
            {"BTC-USDT": 80000}, ts_ms=2,
            last_signal_actions={"vb": "EXIT"},
        )
        assert len(events) == 1
        assert "signal_exit" in events[0].exit_reason

    def test_no_fire_on_hold(self, portfolio, manager):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1,
            (SignalReversal("vb"),),
        )
        events = manager.check_exits(
            {"BTC-USDT": 80000}, ts_ms=2,
            last_signal_actions={"vb": "HOLD"},
        )
        assert events == []


# ─── Realized PnL accumulates correctly ─────────────────────────────────────


class TestPnLAccumulation:
    def test_pnl_after_exits(self, portfolio, manager):
        c1 = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, (TakeProfit(0.006),),
        )
        manager.check_exits({"BTC-USDT": 80800}, ts_ms=2)
        # +1% × 100 - 0.2% × 100 = 0.8
        assert abs(portfolio.realized_pnl_usd() - 0.8) < 0.01
