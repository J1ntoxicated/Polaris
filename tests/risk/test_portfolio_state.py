"""Tests for src/risk/portfolio_state.py — Contribution + AggregatedPosition."""
from __future__ import annotations

import pytest

from src.exec.exit_strategies import StopLoss, TakeProfit, TimeBasedHold
from src.risk.portfolio_state import (
    AggregatedPosition,
    Contribution,
    make_contribution_id,
)


def _contrib(
    cid: str = "BTC1", strategy: str = "vb", size_usd: float = 100.0,
    entry: float = 80000.0, ts: int = 1_700_000_000_000,
) -> Contribution:
    return Contribution(
        contribution_id=cid, ticker="BTC-USDT", strategy_name=strategy,
        hypo_id=f"HYPO-{strategy}", direction=1,
        entry_price=entry, size_usd=size_usd, base_qty=size_usd / entry,
        open_ts_ms=ts, fee_round_trip=0.002,
        exit_strategies=(TakeProfit(0.006), StopLoss(0.0035), TimeBasedHold(4.0)),
    )


# ─── Contribution ────────────────────────────────────────────────────────────


class TestContribution:
    def test_create(self):
        c = _contrib()
        assert c.size_usd == 100.0
        assert c.base_qty == 100.0 / 80000.0
        assert not c.is_closed

    def test_invalid_entry_raises(self):
        with pytest.raises(ValueError):
            Contribution(
                contribution_id="X", ticker="BTC-USDT", strategy_name="x",
                hypo_id="X", direction=1, entry_price=0,
                size_usd=100, base_qty=1, open_ts_ms=1,
                fee_round_trip=0.002,
            )

    def test_with_high_since_entry(self):
        c = _contrib()
        c2 = c.with_high_since_entry(80500)
        assert c2.high_since_entry == 80500
        # No update when price lower
        c3 = c2.with_high_since_entry(80100)
        assert c3.high_since_entry == 80500

    def test_with_partial_fired(self):
        c = _contrib()
        c2 = c.with_partial_fired(0.005)
        assert 0.005 in c2.fired_partial_levels
        # Idempotent
        c3 = c2.with_partial_fired(0.005)
        assert c3.fired_partial_levels == (0.005,)

    def test_full_close(self):
        c = _contrib(size_usd=100, entry=80000)
        remaining, closed = c.closed(
            exit_price=80800, close_ts_ms=1_700_000_001_000,
            reason="tp_hit", fraction=1.0,
        )
        # Closed slice has all the size
        assert closed.size_usd == 100
        assert closed.is_closed
        assert closed.exit_price == 80800
        # Net = +1% × 100 - 0.002 × 100 = 1 - 0.2 = 0.8
        assert abs(closed.realized_net_usd - 0.8) < 0.001
        # Remaining is sentinel
        assert remaining.size_usd == 0
        assert remaining.is_closed

    def test_partial_close(self):
        c = _contrib(size_usd=100, entry=80000)
        remaining, closed = c.closed(
            exit_price=80400, close_ts_ms=1_700_000_001_000,
            reason="partial_tp:0.5%", fraction=0.5,
        )
        # Half closed
        assert closed.size_usd == 50
        assert remaining.size_usd == 50
        assert not remaining.is_closed
        assert closed.is_closed
        # Closed PnL: +0.5% × 50 - 0.002 × 50 = 0.25 - 0.1 = 0.15
        assert abs(closed.realized_net_usd - 0.15) < 0.001

    def test_invalid_fraction_raises(self):
        c = _contrib()
        with pytest.raises(ValueError):
            c.closed(80000, 1, "x", fraction=0)
        with pytest.raises(ValueError):
            c.closed(80000, 1, "x", fraction=1.5)

    def test_unrealized_pct(self):
        c = _contrib(entry=80000)
        assert abs(c.unrealized_pct(80800) - 0.01) < 1e-6
        assert abs(c.unrealized_pct(79200) - (-0.01)) < 1e-6

    def test_unrealized_usd(self):
        c = _contrib(size_usd=100, entry=80000)
        assert abs(c.unrealized_usd(80800) - 1.0) < 1e-6  # +1% × $100

    def test_immutable(self):
        c = _contrib()
        with pytest.raises(Exception):
            c.size_usd = 200  # type: ignore[misc]


# ─── AggregatedPosition ──────────────────────────────────────────────────────


class TestAggregatedPosition:
    def test_empty_position(self):
        p = AggregatedPosition(ticker="BTC-USDT")
        assert p.total_size_usd == 0
        assert p.total_base_qty == 0
        assert p.n_open == 0
        assert p.avg_entry_price == 0

    def test_add_contribution(self):
        p = AggregatedPosition(ticker="BTC-USDT")
        c = _contrib(cid="X", size_usd=100, entry=80000)
        p2 = p.add_contribution(c)
        assert p2.n_open == 1
        assert p2.total_size_usd == 100
        assert p2.avg_entry_price == 80000

    def test_multiple_contributions_avg(self):
        # 2 contributions: $100@80000 + $200@80050
        # base_qty: 100/80000=0.00125 + 200/80050=0.002499...
        # avg = total_cost / total_qty
        c1 = _contrib(cid="A", size_usd=100, entry=80000)
        c2 = _contrib(cid="B", strategy="grid", size_usd=200, entry=80050)
        p = AggregatedPosition(ticker="BTC-USDT").add_contribution(c1).add_contribution(c2)
        assert p.n_open == 2
        assert p.total_size_usd == 300
        assert 80000 < p.avg_entry_price < 80050

    def test_wrong_ticker_raises(self):
        p = AggregatedPosition(ticker="BTC-USDT")
        c = Contribution(
            contribution_id="X", ticker="ETH-USDT", strategy_name="x",
            hypo_id="X", direction=1, entry_price=2000,
            size_usd=100, base_qty=0.05, open_ts_ms=1,
            fee_round_trip=0.002,
        )
        with pytest.raises(ValueError, match="ticker"):
            p.add_contribution(c)

    def test_replace_contribution(self):
        c = _contrib(cid="X", size_usd=100)
        p = AggregatedPosition(ticker="BTC-USDT").add_contribution(c)
        c2 = c.with_high_since_entry(80500)
        p2 = p.replace_contribution("X", c2)
        assert p2.contributions[0].high_since_entry == 80500

    def test_remove_contribution(self):
        c1 = _contrib(cid="A", size_usd=100)
        c2 = _contrib(cid="B", strategy="grid", size_usd=200)
        p = AggregatedPosition(ticker="BTC-USDT").add_contribution(c1).add_contribution(c2)
        p2 = p.remove_contribution("A")
        assert p2.n_open == 1
        assert p2.contributions[0].contribution_id == "B"

    def test_closed_contributions_excluded_from_totals(self):
        c1 = _contrib(cid="A", size_usd=100)
        p = AggregatedPosition(ticker="BTC-USDT").add_contribution(c1)
        # Replace c1 with closed version
        _, closed = c1.closed(80800, 1, "tp", fraction=1.0)
        p2 = p.replace_contribution("A", closed)
        # Closed contribution doesn't count in active totals
        assert p2.total_size_usd == 0
        assert p2.n_open == 0


# ─── make_contribution_id ────────────────────────────────────────────────────


class TestContributionId:
    def test_format(self):
        cid = make_contribution_id("BTC-USDT", "volume_burst", 1700000000000)
        # No dashes, alphanumeric, ≤32 chars
        assert "-" not in cid
        assert len(cid) <= 32
        assert "BTC" in cid

    def test_long_strategy_truncated(self):
        cid = make_contribution_id("BTC-USDT", "very_long_strategy_name_here", 1)
        assert len(cid) <= 32

    def test_unique_per_timestamp(self):
        a = make_contribution_id("BTC-USDT", "vb", 1700000000000)
        b = make_contribution_id("BTC-USDT", "vb", 1700000000001)
        assert a != b
