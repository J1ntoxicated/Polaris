"""Tests for src/risk/position_policy.py + adaptive_policies + exit_merger."""
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
from src.risk.adaptive_policies import (
    MergeAdaptivePolicy,
    RegimeAdaptivePolicy,
    TrailingProfitPolicy,
    build_default_composite,
)
from src.risk.exit_merger import merge_exits
from src.risk.portfolio_manager import PortfolioManager
from src.risk.position_policy import (
    CompositePolicy,
    MarketContext,
    PolicyAction,
    PolicyDecision,
    StaticPolicy,
)


def _exits_scalp():
    return (TakeProfit(0.006), StopLoss(0.0035), TimeBasedHold(4.0))


def _exits_swing():
    return (TakeProfit(0.05), StopLoss(0.02), TimeBasedHold(168.0))


@pytest.fixture
def portfolio():
    return PortfolioManager(starting_cash_usd=5000.0)


def _ctx(price=100.0, regime="flat", ts=1):
    return MarketContext(ticker="BTC-USDT", price=price, ts_ms=ts, regime=regime)


# ─── StaticPolicy (Phase 20 behavior preserved) ──────────────────────────────


class TestStaticPolicy:
    def test_always_holds(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        pos = portfolio.get_position("BTC-USDT")
        d = StaticPolicy().evaluate(pos, _ctx())
        assert d.action == PolicyAction.HOLD


# ─── PolicyDecision validation ───────────────────────────────────────────────


class TestPolicyDecision:
    def test_update_requires_new_exits(self):
        with pytest.raises(ValueError):
            PolicyDecision(action=PolicyAction.UPDATE_EXITS, new_exits=())

    def test_partial_requires_valid_fraction(self):
        with pytest.raises(ValueError):
            PolicyDecision(action=PolicyAction.EXIT_PARTIAL, fraction=0)
        with pytest.raises(ValueError):
            PolicyDecision(action=PolicyAction.EXIT_PARTIAL, fraction=1.5)

    def test_hold_minimal(self):
        d = PolicyDecision(action=PolicyAction.HOLD)
        assert d.fraction == 1.0
        assert d.new_exits == ()


# ─── ExitMerger ──────────────────────────────────────────────────────────────


class TestExitMerger:
    def test_empty_input(self):
        assert merge_exits([]) == ()

    def test_single_contribution_passthrough(self):
        e = (TakeProfit(0.01), StopLoss(0.005))
        assert merge_exits([e]) == e

    def test_max_tp(self):
        merged = merge_exits([
            (TakeProfit(0.006),),  # scalp
            (TakeProfit(0.05),),   # swing
        ])
        tps = [e for e in merged if isinstance(e, TakeProfit)]
        assert len(tps) == 1
        assert tps[0].pct == 0.05  # bigger target captures full move

    def test_min_sl(self):
        merged = merge_exits([
            (StopLoss(0.0035),),   # tight scalp
            (StopLoss(0.02),),     # loose swing
        ])
        sls = [e for e in merged if isinstance(e, StopLoss)]
        assert sls[0].pct == 0.0035  # tightest = most protective

    def test_max_hold(self):
        merged = merge_exits([
            (TimeBasedHold(4.0),),
            (TimeBasedHold(168.0),),
        ])
        holds = [e for e in merged if isinstance(e, TimeBasedHold)]
        assert holds[0].max_hours == 168.0

    def test_signal_reversals_kept_per_strategy(self):
        merged = merge_exits([
            (SignalReversal("vb"),),
            (SignalReversal("nfi"),),
        ])
        reversals = [e for e in merged if isinstance(e, SignalReversal)]
        assert len(reversals) == 2
        names = {e.strategy_name for e in reversals}
        assert names == {"vb", "nfi"}

    def test_full_merge_scalp_plus_swing(self):
        merged = merge_exits([_exits_scalp(), _exits_swing()])
        # Should have: TP=0.05, SL=0.0035, MaxHold=168.0
        tps = [e for e in merged if isinstance(e, TakeProfit)]
        sls = [e for e in merged if isinstance(e, StopLoss)]
        holds = [e for e in merged if isinstance(e, TimeBasedHold)]
        assert tps[0].pct == 0.05
        assert sls[0].pct == 0.0035
        assert holds[0].max_hours == 168.0


# ─── MergeAdaptivePolicy ────────────────────────────────────────────────────


class TestMergeAdaptivePolicy:
    def test_single_contrib_holds(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        p = MergeAdaptivePolicy()
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx())
        assert d.action == PolicyAction.HOLD

    def test_two_contribs_triggers_unify(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        portfolio.process_entry("BTC-USDT", "nfi", "Y", 100, 80050, 2, _exits_swing())
        p = MergeAdaptivePolicy()
        # First eval after merge
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx())
        assert d.action == PolicyAction.UPDATE_EXITS
        # Unified: TP max=0.05, SL min=0.0035
        tps = [e for e in d.new_exits if isinstance(e, TakeProfit)]
        sls = [e for e in d.new_exits if isinstance(e, StopLoss)]
        assert tps[0].pct == 0.05
        assert sls[0].pct == 0.0035

    def test_idempotent_after_merge(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        portfolio.process_entry("BTC-USDT", "nfi", "Y", 100, 80050, 2, _exits_swing())
        p = MergeAdaptivePolicy()
        d1 = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx())
        assert d1.action == PolicyAction.UPDATE_EXITS
        # Second eval — no new merge → HOLD
        d2 = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx())
        assert d2.action == PolicyAction.HOLD


# ─── RegimeAdaptivePolicy ───────────────────────────────────────────────────


class TestRegimeAdaptivePolicy:
    def test_no_change_holds(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        p = RegimeAdaptivePolicy()
        # First eval at flat — exits_for_regime returns () for flat → HOLD
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(regime="flat"))
        assert d.action == PolicyAction.HOLD

    def test_uptrend_loosens(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        p = RegimeAdaptivePolicy()
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(regime="uptrend"))
        assert d.action == PolicyAction.UPDATE_EXITS
        tps = [e for e in d.new_exits if isinstance(e, TakeProfit)]
        assert tps[0].pct == 0.08  # bigger TP for uptrend

    def test_crisis_tightens(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        p = RegimeAdaptivePolicy()
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(regime="crisis"))
        assert d.action == PolicyAction.UPDATE_EXITS
        sls = [e for e in d.new_exits if isinstance(e, StopLoss)]
        assert sls[0].pct == 0.005  # tighter SL for crisis

    def test_idempotent_same_regime(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        p = RegimeAdaptivePolicy()
        d1 = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(regime="uptrend"))
        d2 = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(regime="uptrend"))
        assert d1.action == PolicyAction.UPDATE_EXITS
        assert d2.action == PolicyAction.HOLD


# ─── TrailingProfitPolicy ───────────────────────────────────────────────────


class TestTrailingProfitPolicy:
    def test_no_fire_below_activation(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        p = TrailingProfitPolicy(activation_pct=0.005)
        # +0.3% gain — below 0.5% activation
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(price=80240))
        assert d.action == PolicyAction.HOLD

    def test_fires_at_activation(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        p = TrailingProfitPolicy(activation_pct=0.005, trail_pct=0.003)
        # +0.6% gain
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(price=80480))
        assert d.action == PolicyAction.UPDATE_EXITS
        # Trailing in new exits
        trails = [e for e in d.new_exits if isinstance(e, TrailingStop)]
        assert len(trails) == 1
        assert trails[0].trail_pct == 0.003

    def test_idempotent_after_activation(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        p = TrailingProfitPolicy(activation_pct=0.005, trail_pct=0.003)
        d1 = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(price=80480))
        d2 = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(price=80800))
        assert d1.action == PolicyAction.UPDATE_EXITS
        assert d2.action == PolicyAction.HOLD


# ─── CompositePolicy ────────────────────────────────────────────────────────


class TestCompositePolicy:
    def test_first_non_hold_wins(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        portfolio.process_entry("BTC-USDT", "nfi", "Y", 100, 80050, 2, _exits_swing())
        # Default composite: Merge → Regime → Trailing
        p = build_default_composite()
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(regime="flat"))
        assert d.action == PolicyAction.UPDATE_EXITS
        # Merge fired first → unified exits
        assert "merge" in d.reason

    def test_all_hold_returns_hold(self, portfolio):
        portfolio.process_entry("BTC-USDT", "vb", "X", 100, 80000, 1, _exits_scalp())
        # Single contribution + flat regime + low gain = all hold
        p = build_default_composite()
        d = p.evaluate(portfolio.get_position("BTC-USDT"), _ctx(price=80100, regime="flat"))
        assert d.action == PolicyAction.HOLD
