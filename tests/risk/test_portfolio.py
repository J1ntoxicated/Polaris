"""Tests for src/risk/portfolio.py — portfolio aggregation + correlation."""
from __future__ import annotations

import time

import pytest

from src.paper.state import PaperBalance, Position
from src.persist.ledger import TradeLedger
from src.risk.portfolio import (
    PortfolioSnapshot,
    _pearson,
    attribution_by_hypo,
    compute_correlation_matrix,
    compute_portfolio_snapshot,
    should_halt_portfolio,
)


@pytest.fixture
def ledger():
    led = TradeLedger(":memory:")
    led.open()
    yield led
    led.close()


def _open_pos(ticker, ts_ms, size=300.0, price=100.0):
    return Position(
        position_id=f"{ticker}-{ts_ms}",
        ticker=ticker, direction=1,
        entry_price=price, size_usd=size, open_ts_ms=ts_ms,
        fee_round_trip=0.002,
    )


# ─── Snapshot ────────────────────────────────────────────────────────────────


class TestPortfolioSnapshot:
    def test_empty_ledger_zero(self, ledger):
        snap = compute_portfolio_snapshot(ledger, ts_ms=1)
        assert snap.total_equity_usd == 0.0
        assert snap.total_realized_usd == 0.0
        assert snap.total_open_count == 0
        assert snap.drawdown_pct == 0.0

    def test_balance_aggregates_cash(self, ledger):
        # Two HYPOs with starting $5000 each, all in cash
        for hid in ("HYPO-A", "HYPO-B"):
            bal = PaperBalance(starting_usd=5000.0, cash_usd=5000.0)
            ledger.upsert_balance(hid, "BTC-USDT", bal)
        snap = compute_portfolio_snapshot(ledger, ts_ms=1)
        assert snap.total_equity_usd == 10000.0
        assert snap.n_active_hypos == 2

    def test_open_position_mtm_increase(self, ledger):
        # $300 open, +1% price → equity = cash + size×1.01
        bal = PaperBalance(starting_usd=5000.0, cash_usd=4700.0)
        ledger.upsert_balance("HYPO-X", "BTC-USDT", bal)
        pos = _open_pos("BTC-USDT", 1_700_000_000_000, size=300.0, price=80000.0)
        ledger.insert_position_open(pos, "HYPO-X", "test")
        snap = compute_portfolio_snapshot(
            ledger, current_prices={"BTC-USDT": 80800.0}, ts_ms=1,
        )
        # 4700 + 300 × 1.01 = 5003.0
        assert abs(snap.total_equity_usd - 5003.0) < 0.01

    def test_drawdown_vs_high_water_mark(self, ledger):
        # First snapshot establishes hwm = 10000
        ledger.insert_portfolio_snapshot(
            ts_ms=1, total_equity_usd=10000.0, total_open=0,
            total_realized=0, drawdown_pct=0,
        )
        bal = PaperBalance(starting_usd=10000.0, cash_usd=9500.0)
        ledger.upsert_balance("HYPO-X", "BTC-USDT", bal)
        snap = compute_portfolio_snapshot(ledger, ts_ms=2)
        # Equity 9500, hwm 10000 → drawdown 5%
        assert abs(snap.drawdown_pct - 0.05) < 0.001
        assert snap.high_water_mark_usd == 10000.0


class TestHaltGate:
    def test_no_halt_below_cap(self):
        snap = PortfolioSnapshot(
            ts_ms=1, total_equity_usd=10000, total_realized_usd=0,
            total_open_count=0, drawdown_pct=0.03,
            high_water_mark_usd=10300, n_active_hypos=2,
        )
        halt, reason = should_halt_portfolio(snap, max_drawdown_pct=0.05)
        assert not halt
        assert reason == ""

    def test_halt_at_cap(self):
        snap = PortfolioSnapshot(
            ts_ms=1, total_equity_usd=9500, total_realized_usd=0,
            total_open_count=0, drawdown_pct=0.05,
            high_water_mark_usd=10000, n_active_hypos=2,
        )
        halt, reason = should_halt_portfolio(snap, max_drawdown_pct=0.05)
        assert halt
        assert "drawdown" in reason
        assert "5.00%" in reason

    def test_halt_above_cap(self):
        snap = PortfolioSnapshot(
            ts_ms=1, total_equity_usd=8500, total_realized_usd=0,
            total_open_count=0, drawdown_pct=0.15,
            high_water_mark_usd=10000, n_active_hypos=2,
        )
        halt, _ = should_halt_portfolio(snap, max_drawdown_pct=0.05)
        assert halt


# ─── Correlation ─────────────────────────────────────────────────────────────


class TestPearson:
    def test_perfect_positive(self):
        assert abs(_pearson([1, 2, 3, 4], [2, 4, 6, 8]) - 1.0) < 1e-9

    def test_perfect_negative(self):
        assert abs(_pearson([1, 2, 3, 4], [8, 6, 4, 2]) - (-1.0)) < 1e-9

    def test_zero_variance_returns_zero(self):
        assert _pearson([1, 1, 1], [2, 2, 2]) == 0.0

    def test_short_series_returns_zero(self):
        assert _pearson([1], [2]) == 0.0
        assert _pearson([], []) == 0.0

    def test_no_correlation(self):
        # Independent random-ish series
        r = _pearson([1, -1, 1, -1], [1, 1, -1, -1])
        assert abs(r) < 1e-9


class TestCorrelationMatrix:
    def test_self_correlation_one(self, ledger):
        # Insert closed positions in 2 different hour buckets with varying PnL
        now = int(time.time() * 1000)
        # Bucket 1: large win
        pos = _open_pos("BTC-USDT", now - 7200_000)
        ledger.insert_position_open(pos, "HYPO-X", "test")
        ledger.update_position_close(
            position_id=pos.position_id,
            exit_price=103.0, close_ts_ms=now - 7000_000,  # +3% win
            exit_reason="tp",
        )
        # Bucket 2: small loss (different value → variance > 0)
        pos2 = _open_pos("ETH-USDT", now - 3600_000)
        ledger.insert_position_open(pos2, "HYPO-X", "test")
        ledger.update_position_close(
            position_id=pos2.position_id,
            exit_price=99.5, close_ts_ms=now - 3500_000,  # -0.5% small loss
            exit_reason="sl",
        )
        corr = compute_correlation_matrix(ledger, ["HYPO-X"], window_h=24, now_ms=now)
        # Self-correlation = 1.0 with non-zero variance
        assert abs(corr[("HYPO-X", "HYPO-X")] - 1.0) < 1e-9


# ─── Attribution ─────────────────────────────────────────────────────────────


class TestAttribution:
    def test_attribution_per_hypo(self, ledger):
        now = int(time.time() * 1000)
        # HYPO-A: 1 win
        for i, (hid, gain) in enumerate([
            ("HYPO-A", 0.01),  # +1% (win)
            ("HYPO-A", 0.02),  # +2% (win)
            ("HYPO-B", -0.01), # -1% (loss)
        ]):
            tk = f"BTC-USDT-{i}"
            pos = _open_pos(tk, now - 1000 - i, price=100.0)
            ledger.insert_position_open(pos, hid, "test")
            ledger.update_position_close(
                position_id=pos.position_id,
                exit_price=100.0 * (1 + gain),
                close_ts_ms=now - 100 - i,
                exit_reason="tp" if gain > 0 else "sl",
            )
        attr = attribution_by_hypo(ledger)
        names = [a["hypo_id"] for a in attr]
        assert names == ["HYPO-A", "HYPO-B"]  # sorted by net_usd desc
        a = next(a for a in attr if a["hypo_id"] == "HYPO-A")
        assert a["n_trades"] == 2
        assert a["n_wins"] == 2
        assert a["win_rate"] == 1.0
