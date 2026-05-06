"""Tests for Phase 22 — profit cycling architecture.

Covers MicroProfitPolicy + DailyTargetTracker + CapitalVelocity.
"""
from __future__ import annotations

import pytest

from src.exec.exit_strategies import StopLoss, TakeProfit, TimeBasedHold
from src.risk.adaptive_policies import (
    MicroProfitPolicy,
    build_aggressive_composite,
    build_default_composite,
)
from src.risk.capital_velocity import compute_velocity
from src.risk.daily_target import (
    DEFAULT_DAILY_TARGET_PCT,
    compute_daily_progress,
)
from src.risk.portfolio_manager import PortfolioManager
from src.risk.position_policy import (
    CompositePolicy,
    MarketContext,
    PolicyAction,
)


def _exits():
    return (TakeProfit(0.05), StopLoss(0.02), TimeBasedHold(168.0))


@pytest.fixture
def portfolio():
    return PortfolioManager(starting_cash_usd=5000.0)


def _ctx(price=100.0, ts=1):
    return MarketContext(ticker="BTC-USDT", price=price, ts_ms=ts)


# ─── MicroProfitPolicy ──────────────────────────────────────────────────────


class TestMicroProfit:
    def test_no_fire_below_threshold(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits())
        p = MicroProfitPolicy(min_profit_pct=0.005)
        # +0.3% gain
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(price=80240))
        assert d.action == PolicyAction.HOLD

    def test_fires_at_threshold(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits())
        p = MicroProfitPolicy(min_profit_pct=0.005)
        # +0.6% gain
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(price=80480))
        assert d.action == PolicyAction.EXIT_FULL
        assert "micro_profit" in d.reason

    def test_aggressive_threshold(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits())
        p = MicroProfitPolicy(min_profit_pct=0.003)
        # +0.4% gain — fires at aggressive threshold
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(price=80320))
        assert d.action == PolicyAction.EXIT_FULL

    def test_weighted_unrealized_multi_contrib(self, portfolio):
        # 2 contributions: $100@80000 (early), $100@80050 (later)
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits())
        portfolio.process_entry("BTC-USDT", "nfi", "Y", 100, 80050, 2, _exits())
        # At 80800: vb +1%, nfi +0.94%, weighted ~+0.97%
        p = MicroProfitPolicy(min_profit_pct=0.005)
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(price=80800))
        assert d.action == PolicyAction.EXIT_FULL


# ─── DailyTargetTracker ─────────────────────────────────────────────────────


class TestDailyTarget:
    def test_zero_progress_initial(self, portfolio):
        # ts at start of UTC day
        progress = compute_daily_progress(portfolio, {}, ts_ms=1_700_000_000_000)
        assert progress.target_usd == 25.0  # 5000 × 0.005
        assert progress.actual_today_usd == 0.0
        assert progress.progress_ratio == 0.0
        assert not progress.on_track

    def test_realized_today_counted(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000,
            ts_ms=1_700_000_000_000, exit_strategies=_exits(),
        )
        # Exit at +1% → net = 0.8
        portfolio.partial_close(
            c.contribution_id, exit_price=80800,
            ts_ms=1_700_000_000_000 + 60_000,
            reason="tp", fraction=1.0,
        )
        progress = compute_daily_progress(
            portfolio, {}, ts_ms=1_700_000_000_000 + 120_000,
        )
        assert abs(progress.realized_today_usd - 0.8) < 0.01
        assert progress.n_trades_today == 1

    def test_unrealized_counted(self, portfolio):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000,
            ts_ms=1_700_000_000_000, exit_strategies=_exits(),
        )
        # +1% unrealized minus 0.2% fee (net) = 0.8
        progress = compute_daily_progress(
            portfolio, {"BTC-USDT": 80800},
            ts_ms=1_700_000_000_000 + 60_000,
        )
        assert abs(progress.unrealized_usd - 0.8) < 0.01

    def test_on_track(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 1000, 80000,
            ts_ms=1_700_000_000_000, exit_strategies=_exits(),
        )
        # +5% net = 5000 × 0.05 - 0.002 × 1000 = 50 - 2 = 48 (way over $25 target)
        portfolio.partial_close(
            c.contribution_id, exit_price=84000,
            ts_ms=1_700_000_000_000 + 60_000,
            reason="tp", fraction=1.0,
        )
        progress = compute_daily_progress(
            portfolio, {}, ts_ms=1_700_000_000_000 + 120_000,
        )
        assert progress.on_track
        assert progress.progress_ratio > 1.0


# ─── CapitalVelocity ────────────────────────────────────────────────────────


class TestCapitalVelocity:
    def test_full_cash_high_idle(self, portfolio):
        v = compute_velocity(portfolio, ts_ms=1_700_000_000_000)
        assert v.cash_idle_ratio == 1.0  # all cash, no positions
        assert v.n_open == 0
        assert v.turnover_today == 0
        assert "HIGH_IDLE" in v.diagnosis

    def test_active_lower_idle(self, portfolio):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 1000, 80000,
            ts_ms=1_700_000_000_000, exit_strategies=_exits(),
        )
        # 4000 cash + 1000 position = 80% cash idle
        v = compute_velocity(portfolio, ts_ms=1_700_000_000_000 + 60_000)
        assert abs(v.cash_idle_ratio - 0.8) < 0.01
        assert v.n_open == 1
        assert v.avg_position_age_min == 1.0

    def test_high_turnover_ok_diagnosis(self, portfolio):
        # 5 closed today + open new positions to lower idle ratio
        for i in range(5):
            c = portfolio.process_entry(
                f"BTC-USDT" if i % 2 == 0 else "ETH-USDT", "vb", "X",
                100, 80000, ts_ms=1_700_000_000_000 + i * 1000,
                exit_strategies=_exits(),
            )
            portfolio.partial_close(
                c.contribution_id, exit_price=80800,
                ts_ms=1_700_000_000_000 + i * 1000 + 500,
                reason="tp", fraction=1.0,
            )
        # Open 2 active positions to drop idle ratio (per-ticker cap $1500)
        portfolio.process_entry(
            "BTC-USDT", "nfi", "Y", 1500, 80000,
            ts_ms=1_700_000_000_000 + 50_000, exit_strategies=_exits(),
        )
        portfolio.process_entry(
            "ETH-USDT", "grid", "Z", 1000, 2000,
            ts_ms=1_700_000_000_000 + 51_000, exit_strategies=_exits(),
        )
        v = compute_velocity(portfolio, ts_ms=1_700_000_000_000 + 100_000)
        assert v.turnover_today == 5
        # cash ~2500, open ~2500 → idle 50%
        assert v.cash_idle_ratio < 0.7
        assert v.diagnosis == "OK"


# ─── Default composite uses MicroProfit (no Trailing) ──────────────────────


class TestDefaultComposite:
    def test_default_includes_micro_profit(self):
        comp = build_default_composite()
        assert isinstance(comp, CompositePolicy)
        names = [p.name for p in comp.policies]
        assert "merge_adaptive" in names
        assert "micro_profit" in names
        assert "regime_adaptive" in names
        assert "trailing_profit" not in names  # Phase 22 removed

    def test_aggressive_lower_threshold(self):
        comp = build_aggressive_composite()
        # Find micro_profit
        micro = next(p for p in comp.policies if p.name == "micro_profit")
        assert micro.min_profit_pct == 0.003


# ─── End-to-end: micro profit fires before regime can adapt ─────────────────


class TestMicroProfitWinsOverRegime:
    """Phase 22 user philosophy: micro profit beats regime adaptation."""

    def test_micro_profit_fires_before_regime_change(self, portfolio):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        comp = build_default_composite()
        # +0.6% gain, uptrend regime
        ctx = MarketContext(
            ticker="BTC-USDT", price=80480, ts_ms=2, regime="uptrend",
        )
        d = comp.evaluate(portfolio.get_position("BTC-USDT"), ctx)
        assert d.action == PolicyAction.EXIT_FULL
        assert "micro_profit" in d.reason
