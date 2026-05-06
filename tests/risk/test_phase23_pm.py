"""Tests for Phase 23 PM layer — OpportunityScanner + ReallocationDecider."""
from __future__ import annotations

import pytest

from src.exec.exit_strategies import StopLoss, TakeProfit, TimeBasedHold
from src.risk.opportunity_scanner import (
    Opportunity,
    OpportunityScanner,
    ScanResult,
)
from src.risk.portfolio_manager import PortfolioManager
from src.risk.position_evaluator import (
    EvaluationInputs,
    PositionEvaluation,
    PositionState,
)
from src.risk.reallocation_decider import (
    ReallocAction,
    ReallocationDecider,
)


def _exits():
    return (TakeProfit(0.05), StopLoss(0.02), TimeBasedHold(168.0))


def _opp(ticker="ETH-USDT", strat="grid", er=0.012, conf=0.8):
    return Opportunity(
        ticker=ticker, strategy_name=strat, hypo_id=f"HYPO-{strat}",
        signal_confidence=conf, historical_ev_pct=0.015,
        expected_return_pct=er, signal_reason="test",
        ts_ms=1_700_000_000_000,
    )


def _eval(state=PositionState.WARM, score=0.4, ev=0.004):
    return PositionEvaluation(
        score=score, state=state,
        reason="test",
        forward_ev_pct=ev,
        inputs=EvaluationInputs(0.0, 0.0, 0.0),
    )


@pytest.fixture
def portfolio():
    return PortfolioManager(starting_cash_usd=5000.0)


# ─── OpportunityScanner ─────────────────────────────────────────────────────


class TestOpportunityScanner:
    def test_throttle_no_rescan_within_interval(self):
        s = OpportunityScanner(scan_interval_s=30.0)
        # First scan
        s.scan(ts_ms=1000, candidates=[("BTC", "vb", "X")],
               signal_fn=lambda *a: _opp())
        # 10s later — should not re-scan
        assert not s.should_rescan(11000)

    def test_rescan_after_interval(self):
        s = OpportunityScanner(scan_interval_s=30.0)
        s.scan(ts_ms=1000, candidates=[("BTC", "vb", "X")],
               signal_fn=lambda *a: _opp())
        # 31s later — re-scan
        assert s.should_rescan(32000)

    def test_force_rescan(self):
        s = OpportunityScanner(scan_interval_s=300.0)
        r1 = s.scan(ts_ms=1000, candidates=[("BTC", "vb", "X")],
                    signal_fn=lambda *a: _opp(er=0.01))
        # Force re-scan with different result
        r2 = s.scan(ts_ms=1500, candidates=[("BTC", "vb", "X")],
                    signal_fn=lambda *a: _opp(er=0.05), force=True)
        assert r1.opportunities[0].expected_return_pct == 0.01
        assert r2.opportunities[0].expected_return_pct == 0.05

    def test_ranking_by_er(self):
        s = OpportunityScanner()
        opps_iter = iter([
            _opp(ticker="BTC", er=0.005),
            _opp(ticker="ETH", er=0.012),
            _opp(ticker="SOL", er=0.008),
        ])

        def _fn(t, sn, h):
            return next(opps_iter)
        result = s.scan(ts_ms=1000, candidates=[
            ("BTC", "vb", "X"), ("ETH", "vb", "Y"), ("SOL", "vb", "Z"),
        ], signal_fn=_fn)
        # ETH (0.012) first, SOL (0.008), BTC (0.005)
        assert result.opportunities[0].ticker == "ETH"
        assert result.opportunities[1].ticker == "SOL"
        assert result.opportunities[2].ticker == "BTC"

    def test_top_n_truncates(self):
        s = OpportunityScanner(top_n=2)
        opps_iter = iter([_opp(ticker=f"T{i}", er=i / 100) for i in range(5)])

        def _fn(t, sn, h):
            return next(opps_iter)
        result = s.scan(ts_ms=1000, candidates=[
            (f"T{i}", "vb", "X") for i in range(5)
        ], signal_fn=_fn)
        assert len(result.opportunities) == 2

    def test_skip_none_signals(self):
        s = OpportunityScanner()
        result = s.scan(
            ts_ms=1000, candidates=[("BTC", "vb", "X"), ("ETH", "vb", "Y")],
            signal_fn=lambda t, sn, h: _opp() if t == "BTC" else None,
        )
        assert result.n_evaluated == 2
        assert result.n_signals == 1

    def test_best_for_ticker(self):
        s = OpportunityScanner()
        s.scan(ts_ms=1000, candidates=[
            ("BTC", "vb", "X"), ("ETH", "vb", "Y"),
        ], signal_fn=lambda t, sn, h: _opp(ticker=t, er=0.01))
        assert s.best_for_ticker("BTC").ticker == "BTC"
        assert s.best_for_ticker("UNKNOWN") is None


# ─── ReallocationDecider ────────────────────────────────────────────────────


class TestReallocLosing:
    def test_losing_holds(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        d = ReallocationDecider().decide_for_position(
            contribution=c,
            evaluation=_eval(state=PositionState.LOSING, score=-0.5),
            unrealized_pct=-0.005,
            best_opportunity=_opp(),
            ts_ms=2,
        )
        # LOSING → HOLD (let SL exit_strategy fire)
        assert d.action == ReallocAction.HOLD


class TestReallocCold:
    def test_cold_with_profit_takes(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        # COLD + +0.6% unrealized → take profit (free capital)
        d = ReallocationDecider(min_profit_take_pct=0.005).decide_for_position(
            contribution=c,
            evaluation=_eval(state=PositionState.COLD, score=0.1, ev=0.001),
            unrealized_pct=0.006,
            best_opportunity=None,
            ts_ms=2,
        )
        assert d.action == ReallocAction.CLOSE_ONLY
        assert "profit_take" in d.reason

    def test_cold_with_better_opp_rotates(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        # COLD position EV +0.1%, opportunity EV +1.5% → 1.4% edge > 0.7% cost
        d = ReallocationDecider().decide_for_position(
            contribution=c,
            evaluation=_eval(state=PositionState.COLD, score=0.1, ev=0.001),
            unrealized_pct=-0.001,  # tiny loss, no profit-take
            best_opportunity=_opp(ticker="ETH", er=0.015),
            ts_ms=2,
        )
        assert d.action == ReallocAction.CLOSE_THEN_OPEN
        assert d.opportunity.ticker == "ETH"

    def test_cold_with_marginal_opp_holds(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        # COLD ev +0.1%, opp ev +0.5% → 0.4% edge < 0.7% cost
        d = ReallocationDecider(switching_cost_pct=0.007).decide_for_position(
            contribution=c,
            evaluation=_eval(state=PositionState.COLD, score=0.1, ev=0.001),
            unrealized_pct=-0.001,
            best_opportunity=_opp(ticker="ETH", er=0.005),
            ts_ms=2,
        )
        assert d.action == ReallocAction.HOLD

    def test_cold_cooldown_prevents_rotate(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        decider = ReallocationDecider(rotate_cooldown_s=300)
        # First rotate
        decider.decide_for_position(
            contribution=c, evaluation=_eval(state=PositionState.COLD, ev=0.001),
            unrealized_pct=-0.001, best_opportunity=_opp(er=0.015), ts_ms=1000,
        )
        # 2 min later — still in cooldown (300s)
        c2 = portfolio.process_entry(
            "BTC-USDT", "nfi", "Y", 100, 80050, 121_000, _exits(),
        )
        d2 = decider.decide_for_position(
            contribution=c2, evaluation=_eval(state=PositionState.COLD, ev=0.001),
            unrealized_pct=-0.001, best_opportunity=_opp(er=0.015), ts_ms=121_000,
        )
        # Cooldown blocks rotate
        assert d2.action == ReallocAction.HOLD


class TestReallocHot:
    def test_hot_too_fresh_no_add(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1_000_000, _exits(),
        )
        d = ReallocationDecider().decide_for_position(
            contribution=c,
            evaluation=_eval(state=PositionState.HOT, score=0.7),
            unrealized_pct=0.003,
            best_opportunity=_opp(ticker="BTC-USDT", strat="vb", conf=0.8),
            ts_ms=1_000_000 + 60_000,  # 1 min held — too fresh
        )
        assert d.action == ReallocAction.HOLD
        assert "fresh" in d.reason

    def test_hot_stable_adds(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1_000_000, _exits(),
        )
        d = ReallocationDecider().decide_for_position(
            contribution=c,
            evaluation=_eval(state=PositionState.HOT, score=0.7),
            unrealized_pct=0.003,
            best_opportunity=_opp(ticker="BTC-USDT", strat="vb", conf=0.85, er=0.02),
            ts_ms=1_000_000 + 360_000,  # 6 min held → stable
        )
        assert d.action == ReallocAction.ADD_TO
        assert d.add_size_usd > 0
        assert d.add_size_usd <= 50  # ≤50% of original 100

    def test_hot_low_conf_opp_no_add(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1_000_000, _exits(),
        )
        d = ReallocationDecider().decide_for_position(
            contribution=c,
            evaluation=_eval(state=PositionState.HOT, score=0.7),
            unrealized_pct=0.003,
            best_opportunity=_opp(ticker="BTC-USDT", strat="vb", conf=0.5),  # weak
            ts_ms=1_000_000 + 360_000,
        )
        assert d.action == ReallocAction.HOLD

    def test_hot_different_strategy_no_add(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1_000_000, _exits(),
        )
        d = ReallocationDecider().decide_for_position(
            contribution=c,
            evaluation=_eval(state=PositionState.HOT, score=0.7),
            unrealized_pct=0.003,
            # Different strategy on same ticker — not "thesis re-confirm"
            best_opportunity=_opp(ticker="BTC-USDT", strat="nfi", conf=0.85),
            ts_ms=1_000_000 + 360_000,
        )
        assert d.action == ReallocAction.HOLD


class TestReallocWarm:
    def test_warm_holds(self, portfolio):
        c = portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1, _exits(),
        )
        d = ReallocationDecider().decide_for_position(
            contribution=c,
            evaluation=_eval(state=PositionState.WARM, score=0.4),
            unrealized_pct=0.002,
            best_opportunity=_opp(er=0.02),  # even with great opp
            ts_ms=2,
        )
        # WARM = hold, don't churn
        assert d.action == ReallocAction.HOLD
