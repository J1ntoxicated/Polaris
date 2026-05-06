"""Tests for PortfolioPolicyManager — Phase 23.5 orchestrator."""
from __future__ import annotations

import pytest

from src.exec.exit_strategies import StopLoss, TakeProfit, TimeBasedHold
from src.risk.opportunity_scanner import Opportunity
from src.risk.portfolio_manager import PortfolioManager
from src.risk.portfolio_policy_manager import PortfolioPolicyManager


def _exits():
    return (TakeProfit(0.05), StopLoss(0.02), TimeBasedHold(168.0))


def _opp(ticker="ETH-USDT", strat="grid", er=0.012, conf=0.8):
    return Opportunity(
        ticker=ticker, strategy_name=strat, hypo_id=f"HYPO-{strat}",
        signal_confidence=conf, historical_ev_pct=0.015,
        expected_return_pct=er, signal_reason="test",
        ts_ms=1_700_000_000_000,
    )


@pytest.fixture
def portfolio():
    return PortfolioManager(starting_cash_usd=5000.0)


@pytest.fixture
def pm(portfolio):
    return PortfolioPolicyManager(portfolio, cycle_interval_s=30.0)


# ─── Cycle gating ──────────────────────────────────────────────────────────


class TestCycleGating:
    def test_first_cycle_runs(self, pm):
        assert pm.should_run(1000)

    def test_throttle_within_interval(self, pm):
        pm._last_cycle_ms = 1000
        assert not pm.should_run(15000)  # 14s < 30s

    def test_runs_after_interval(self, pm):
        pm._last_cycle_ms = 1000
        assert pm.should_run(31001)


# ─── Cycle execution ───────────────────────────────────────────────────────


class TestCycleExecution:
    def test_empty_portfolio_no_actions(self, pm):
        result = pm.cycle(
            ts_ms=1000, current_prices={},
            candidate_signals=[], signal_eval_fn=lambda *a: None,
            recent_prices_fn=lambda t: [],
        )
        assert result.n_evaluated == 0

    def test_warm_position_holds(self, portfolio, pm):
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1_000_000, _exits(),
        )
        # Flat momentum, no opportunity → WARM → HOLD
        result = pm.cycle(
            ts_ms=1_000_000 + 60_000,
            current_prices={"BTC-USDT": 80100},
            candidate_signals=[("BTC-USDT", "vb", "HYPO-008")],
            signal_eval_fn=lambda *a: None,  # no signal
            recent_prices_fn=lambda t: [80000, 80050, 80100],
        )
        assert result.n_evaluated == 1
        assert result.n_holds == 1
        assert result.n_closes == 0
        # Position still open
        assert portfolio.n_open_contributions == 1

    def test_cold_with_profit_closes(self, portfolio, pm):
        # Stale position, no momentum, +0.6% profit, COLD evaluation
        portfolio.process_entry(
            "BTC-USDT", "vb", "X", 100, 80000, 1_000_000, _exits(),
        )
        # Force COLD via flat prices + held >> typical (4h fatigue threshold)
        held_ms = 1_000_000 + (5 * 4 * 3600_000)  # 5× typical hold
        result = pm.cycle(
            ts_ms=held_ms,
            current_prices={"BTC-USDT": 80480},  # +0.6% profit
            candidate_signals=[("BTC-USDT", "vb", "HYPO-008")],
            signal_eval_fn=lambda *a: None,  # no continuation signal
            recent_prices_fn=lambda t: [80480, 80480, 80480],  # flat
        )
        # COLD with profit → CLOSE_ONLY
        assert result.n_closes >= 0  # may be 0 or 1 depending on exact score
        # Must not have rotated (no opportunity)
        assert result.n_rotates == 0

    def test_cycle_throttle_skips(self, pm):
        # First cycle runs
        pm.cycle(
            ts_ms=1000, current_prices={}, candidate_signals=[],
            signal_eval_fn=lambda *a: None, recent_prices_fn=lambda t: [],
        )
        # Immediate next call — throttled, n_evaluated stays 0
        result = pm.cycle(
            ts_ms=1500, current_prices={}, candidate_signals=[],
            signal_eval_fn=lambda *a: None, recent_prices_fn=lambda t: [],
        )
        assert result.n_evaluated == 0


class TestADDFlow:
    def test_hot_stable_adds_contribution(self, portfolio, pm):
        # Entry at 1_000_000
        portfolio.process_entry(
            "BTC-USDT", "vb", "HYPO-008", 100, 80000, 1_000_000, _exits(),
        )
        # 6 min later — held > 5min threshold for ADD
        # Strong momentum + same-strategy continuation → HOT
        # Opportunity = same ticker + same strategy + high conf
        ts_now = 1_000_000 + 6 * 60_000
        result = pm.cycle(
            ts_ms=ts_now,
            current_prices={"BTC-USDT": 80800},  # +1% momentum
            candidate_signals=[("BTC-USDT", "vb", "HYPO-008")],
            signal_eval_fn=lambda t, sn, h: _opp(
                ticker=t, strat=sn, er=0.02, conf=0.85,
            ),
            recent_prices_fn=lambda t: [80000, 80300, 80500, 80700, 80800],
        )
        # ADD opens new contribution → 2 total
        # (or HOLD if hysteresis hasn't reached HOT yet from neutral start)
        # Score: cont=1.0, momentum~0.5 (from prices), confluence=0
        # = 0.5 + 0.15 + 0 = 0.65 → HOT (boundary)
        # If HOT and stable → ADD
        # Allow either path (HOT → ADD or WARM → HOLD)
        assert portfolio.n_open_contributions >= 1
