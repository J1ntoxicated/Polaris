"""Tests for src/risk/portfolio_manager.py — single account portfolio."""
from __future__ import annotations

import pytest

from src.exec.exit_strategies import StopLoss, TakeProfit, TimeBasedHold
from src.risk.portfolio_manager import PortfolioConfig, PortfolioManager


def _exits():
    return (TakeProfit(0.006), StopLoss(0.0035), TimeBasedHold(4.0))


@pytest.fixture
def pm():
    return PortfolioManager(starting_cash_usd=5000.0)


# ─── Initialization ──────────────────────────────────────────────────────────


class TestInit:
    def test_starting_cash(self, pm):
        assert pm.cash == 5000.0
        assert pm.starting_cash == 5000.0
        assert pm.n_open_contributions == 0
        assert pm.realized_pnl_usd() == 0.0

    def test_invalid_cash_raises(self):
        with pytest.raises(ValueError):
            PortfolioManager(starting_cash_usd=0)


# ─── Entry processing ────────────────────────────────────────────────────────


class TestEntry:
    def test_basic_entry(self, pm):
        contrib = pm.process_entry(
            ticker="BTC-USDT", strategy_name="vb", hypo_id="HYPO-008",
            size_usd=100.0, fill_price=80000.0, ts_ms=1_700_000_000_000,
            exit_strategies=_exits(),
        )
        assert contrib is not None
        assert pm.cash == 4900.0
        assert pm.n_open_contributions == 1
        assert pm.get_position("BTC-USDT").total_size_usd == 100.0

    def test_two_strategies_same_ticker(self, pm):
        # Critical test — multi-strategy on same ticker
        c1 = pm.process_entry(
            ticker="BTC-USDT", strategy_name="vb", hypo_id="HYPO-008",
            size_usd=100, fill_price=80000, ts_ms=1_700_000_000_000,
            exit_strategies=_exits(),
        )
        c2 = pm.process_entry(
            ticker="BTC-USDT", strategy_name="grid", hypo_id="HYPO-040",
            size_usd=200, fill_price=80050, ts_ms=1_700_000_001_000,
            exit_strategies=_exits(),
        )
        assert c1 is not None and c2 is not None
        assert c1.contribution_id != c2.contribution_id
        # Both contribute to single AggregatedPosition
        pos = pm.get_position("BTC-USDT")
        assert pos.n_open == 2
        assert pos.total_size_usd == 300
        # Each strategy has independent contribution
        contribs = sorted(pos.contributions, key=lambda c: c.size_usd)
        assert contribs[0].strategy_name == "vb"
        assert contribs[1].strategy_name == "grid"

    def test_insufficient_cash_rejects(self, pm):
        contrib = pm.process_entry(
            ticker="BTC-USDT", strategy_name="vb", hypo_id="X",
            size_usd=10000, fill_price=80000, ts_ms=1_700_000_000_000,
            exit_strategies=_exits(),
        )
        assert contrib is None
        assert pm.cash == 5000  # unchanged

    def test_per_ticker_cap_rejects(self):
        pm = PortfolioManager(
            starting_cash_usd=5000,
            config=PortfolioConfig(max_per_ticker_usd=200),
        )
        c1 = pm.process_entry(
            "BTC-USDT", "vb", "X", 150, 80000, 1, _exits(),
        )
        c2 = pm.process_entry(
            "BTC-USDT", "grid", "Y", 100, 80000, 2, _exits(),
        )
        assert c1 is not None
        assert c2 is None  # 150 + 100 > 200 cap
        assert pm.cash == 4850  # only c1 deducted


# ─── Equity calculation ──────────────────────────────────────────────────────


class TestEquity:
    def test_no_positions_equity_is_cash(self, pm):
        assert pm.equity({}) == 5000

    def test_open_position_mtm(self, pm):
        pm.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        # cash 4900 + position 100 × 1.01 = 4900 + 101 = 5001
        eq = pm.equity({"BTC-USDT": 80800})
        assert abs(eq - 5001) < 0.1

    def test_loss_mtm(self, pm):
        pm.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        # cash 4900 + position 100 × 0.99 = 4900 + 99 = 4999
        eq = pm.equity({"BTC-USDT": 79200})
        assert abs(eq - 4999) < 0.1

    def test_equity_no_price_uses_entry(self, pm):
        pm.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        # Without price → conservative entry value
        eq = pm.equity({})
        assert eq == 5000


# ─── Exit/close ─────────────────────────────────────────────────────────────


class TestPartialClose:
    def test_full_close_returns_cash_plus_pnl(self, pm):
        c = pm.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        # Exit at 80800 (+1%) → net = +1% × 100 - 0.2% × 100 = 1 - 0.2 = 0.8
        closed = pm.partial_close(
            c.contribution_id, exit_price=80800, ts_ms=2,
            reason="tp_hit", fraction=1.0,
        )
        assert closed is not None
        assert closed.is_closed
        assert abs(closed.realized_net_usd - 0.8) < 0.01
        # Cash returned: original $100 + net $0.8 = $100.8
        # Cash before close was 4900, after = 4900 + 100 + 0.8 = 5000.8
        assert abs(pm.cash - 5000.8) < 0.01
        # Position fully closed → removed
        assert pm.get_position("BTC-USDT") is None
        assert pm.n_open_contributions == 0

    def test_partial_close_keeps_remaining(self, pm):
        c = pm.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        closed = pm.partial_close(
            c.contribution_id, exit_price=80400, ts_ms=2,
            reason="partial:0.5%", fraction=0.5,
        )
        # Closed half: net = +0.5% × 50 - 0.2% × 50 = 0.25 - 0.1 = 0.15
        assert abs(closed.realized_net_usd - 0.15) < 0.01
        # Position still open with $50 remaining
        pos = pm.get_position("BTC-USDT")
        assert pos.n_open == 1
        assert abs(pos.total_size_usd - 50) < 0.01
        # Cash: 4900 + 50 + 0.15 = 4950.15
        assert abs(pm.cash - 4950.15) < 0.01

    def test_close_unknown_returns_none(self, pm):
        result = pm.partial_close("NONEXISTENT", 100, 1, "x", 1.0)
        assert result is None

    def test_realized_pnl_accumulates(self, pm):
        c = pm.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        pm.partial_close(c.contribution_id, 80800, 2, "tp", 1.0)
        c2 = pm.process_entry(
            "ETH-USDT", "grid", "Y", 100, 2000, 3, _exits(),
        )
        pm.partial_close(c2.contribution_id, 2010, 4, "tp", 1.0)
        # Both wins: $0.8 + $0.3 = $1.1
        # ETH: +0.5% × 100 - 0.2% × 100 = 0.5 - 0.2 = 0.3
        assert abs(pm.realized_pnl_usd() - 1.1) < 0.01
        assert len(pm.closed_contributions()) == 2


# ─── Trailing stop integration (high_since_entry tracking) ──────────────────


class TestHighWaterTracking:
    def test_update_high_water(self, pm):
        c = pm.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        pm.update_high_water("BTC-USDT", 80500)
        contrib, _ = pm.get_contribution(c.contribution_id)
        assert contrib.high_since_entry == 80500
        # Update with higher
        pm.update_high_water("BTC-USDT", 81000)
        contrib, _ = pm.get_contribution(c.contribution_id)
        assert contrib.high_since_entry == 81000
        # No update with lower
        pm.update_high_water("BTC-USDT", 80700)
        contrib, _ = pm.get_contribution(c.contribution_id)
        assert contrib.high_since_entry == 81000

    def test_partial_fired_tracking(self, pm):
        c = pm.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        pm.mark_partial_fired(c.contribution_id, 0.005)
        contrib, _ = pm.get_contribution(c.contribution_id)
        assert 0.005 in contrib.fired_partial_levels


# ─── Multi-strategy independent exits (THE critical test) ───────────────────


class TestIndependentExits:
    def test_one_strategy_exits_other_remains(self, pm):
        """Demo: 2 strategies on BTC, scalp exits at +0.6%, swing keeps holding."""
        # vb (scalp) at $80000 with 0.6% TP
        c_vb = pm.process_entry(
            "BTC-USDT", "vb", "HYPO-008", 100, 80000, 1,
            (TakeProfit(0.006), StopLoss(0.0035)),
        )
        # nfi (swing) at $80050 with 5% TP
        c_nfi = pm.process_entry(
            "BTC-USDT", "nfi", "HYPO-NFI-001", 100, 80050, 2,
            (TakeProfit(0.05), StopLoss(0.02)),
        )
        # Initially 2 open
        assert pm.get_position("BTC-USDT").n_open == 2
        # Price hits 80800 — vb's +1% TP fires for vb, nfi still in HOLD range
        pm.partial_close(c_vb.contribution_id, 80800, 100, "tp_hit", 1.0)
        # vb closed, nfi still open
        pos = pm.get_position("BTC-USDT")
        assert pos.n_open == 1
        assert pos.contributions[0].strategy_name == "nfi"
        assert pos.contributions[0].is_closed is False
