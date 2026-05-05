"""Tests for src/paper/slippage_model.py (P6 pure).

Slippage model = realistic fill price for paper engine matching live execution.
- BUY consumes asks (top → deeper)
- SELL consumes bids (top → deeper)
- Spread cross + market impact internalized
- Fallback chain: book → bid/ask → last × default_slip
"""
from __future__ import annotations

import pytest

from src.paper.slippage_model import (
    DEFAULT_FALLBACK_SLIP,
    compute_fill_price,
    compute_liquidity_cap,
    compute_spread_bps,
    should_skip_entry_spread,
    walk_book,
)


# ─── walk_book — pure orderbook walk ────────────────────────────────────────

class TestWalkBook:
    def test_size_consumed_within_top_level(self):
        # ask: 100 @ 1.0 (= $100 of liquidity)
        # buy $50 → all filled at 100
        levels = [(100.0, 0.5)]  # (price, size_in_base)
        avg, filled_usd = walk_book(levels, target_usd=50.0)
        assert avg == pytest.approx(100.0)
        assert filled_usd == pytest.approx(50.0)

    def test_size_walks_two_levels(self):
        # Level 1: 100 @ 0.5 base = $50 of liquidity
        # Level 2: 101 @ 1.0 base = $101 of liquidity
        # Buy $100 → consume L1 ($50) + $50 from L2 (= 0.495 base)
        # avg = ($50 + $50) / (0.5 + 0.495) ≈ 100.502
        levels = [(100.0, 0.5), (101.0, 1.0)]
        avg, filled = walk_book(levels, target_usd=100.0)
        assert filled == pytest.approx(100.0)
        assert 100.4 < avg < 100.6

    def test_insufficient_liquidity_returns_partial(self):
        # only $50 available, request $200 → partial fill at top level
        levels = [(100.0, 0.5)]
        avg, filled = walk_book(levels, target_usd=200.0)
        assert filled == pytest.approx(50.0)
        assert avg == pytest.approx(100.0)

    def test_empty_book_returns_zero(self):
        avg, filled = walk_book([], target_usd=100.0)
        assert avg == 0.0
        assert filled == 0.0

    def test_zero_target_returns_zero(self):
        levels = [(100.0, 1.0)]
        avg, filled = walk_book(levels, target_usd=0.0)
        assert avg == 0.0
        assert filled == 0.0


# ─── compute_fill_price — full slippage model ──────────────────────────────

class TestComputeFillPrice:
    def test_buy_uses_ask_side_book(self):
        # bids irrelevant for buy
        book = {
            "bids": [(99.0, 1.0)],
            "asks": [(100.0, 0.5), (101.0, 1.0)],
        }
        price, slip_bps = compute_fill_price("buy", size_usd=100.0, book=book, last=99.5)
        # walks 2 levels, avg ~100.5; vs last 99.5 → +1% = 100bps
        assert price > 100.0
        assert slip_bps > 0  # buy slips up

    def test_sell_uses_bid_side_book(self):
        book = {
            "bids": [(99.0, 0.5), (98.0, 1.0)],
            "asks": [(100.0, 1.0)],
        }
        price, slip_bps = compute_fill_price("sell", size_usd=100.0, book=book, last=99.5)
        # walks 2 bid levels, avg < 99; vs last 99.5 → negative slip
        assert price < 99.0
        assert slip_bps > 0  # slippage_bps is always cost magnitude (positive)

    def test_buy_fallback_to_ask_when_book_empty(self):
        # No book → use bid/ask spread
        book = {"bids": [], "asks": []}
        price, slip_bps = compute_fill_price(
            "buy", size_usd=100.0, book=book, last=99.5, bid=99.4, ask=99.6
        )
        assert price == pytest.approx(99.6)  # crosses to ask
        # slip vs last = (99.6 - 99.5) / 99.5 * 10000 ≈ 10bps
        assert 5 < slip_bps < 15

    def test_sell_fallback_to_bid_when_book_empty(self):
        book = {"bids": [], "asks": []}
        price, slip_bps = compute_fill_price(
            "sell", size_usd=100.0, book=book, last=99.5, bid=99.4, ask=99.6
        )
        assert price == pytest.approx(99.4)
        assert 5 < slip_bps < 15

    def test_fallback_to_default_slip_when_no_data(self):
        # Total fallback: only last price available
        price, slip_bps = compute_fill_price(
            "buy", size_usd=100.0, book={}, last=100.0
        )
        # default fallback: last × (1 + DEFAULT_FALLBACK_SLIP)
        assert price == pytest.approx(100.0 * (1 + DEFAULT_FALLBACK_SLIP))
        assert slip_bps == pytest.approx(DEFAULT_FALLBACK_SLIP * 10000)

    def test_invalid_side_raises(self):
        with pytest.raises(ValueError, match="side"):
            compute_fill_price("hold", size_usd=100.0, book={}, last=100.0)

    def test_zero_last_returns_zero(self):
        price, slip_bps = compute_fill_price("buy", size_usd=100.0, book={}, last=0.0)
        assert price == 0.0
        assert slip_bps == 0.0

    def test_buy_partial_fill_blends_with_default_slip(self):
        # Book has only $50 but order is $100 → blended fill
        # Realistic: L1 ask matches book top ask (100.0), no L1 better than book
        book = {"bids": [], "asks": [(100.0, 0.5)]}  # $50 liquidity at 100
        price, slip_bps = compute_fill_price(
            "buy", size_usd=100.0, book=book, last=99.5, bid=99.4, ask=100.0
        )
        # Should reflect partial book fill at 100.0 + remainder at L1 ask (100.0)
        assert price == pytest.approx(100.0)
        # slippage vs last 99.5 = 50bps
        assert 40 < slip_bps < 60


# ─── spread metrics ─────────────────────────────────────────────────────────

class TestSpreadBps:
    def test_normal_spread(self):
        # bid 99.95, ask 100.05, mid 100, spread 0.10 = 10bps
        bps = compute_spread_bps(bid=99.95, ask=100.05)
        assert bps == pytest.approx(10.0, rel=0.01)

    def test_zero_spread(self):
        assert compute_spread_bps(100.0, 100.0) == 0.0

    def test_invalid_returns_inf(self):
        # ask < bid (crossed) → return inf to force skip
        assert compute_spread_bps(101.0, 100.0) == float("inf")

    def test_zero_prices_returns_inf(self):
        assert compute_spread_bps(0.0, 100.0) == float("inf")
        assert compute_spread_bps(100.0, 0.0) == float("inf")


class TestComputeLiquidityCap:
    """Liquidity-aware sizing: cap order size to a fraction of visible book.

    Phase 7 — addresses BLUR-style 10bps slip (size walks book even when
    spread is acceptable). Cap = sum(top-N depth) × max_book_fraction.
    """

    def test_buy_caps_to_ask_depth(self):
        # asks: 100@0.5 ($50) + 101@0.5 ($50.5) = $100.5 total depth
        # cap fraction 0.10 → max $10.05
        book = {"bids": [(99, 1)], "asks": [(100.0, 0.5), (101.0, 0.5)]}
        cap = compute_liquidity_cap("buy", book, max_book_fraction=0.10)
        assert 9 < cap < 11

    def test_sell_caps_to_bid_depth(self):
        book = {"bids": [(99.0, 1.0), (98.0, 1.0)], "asks": [(100, 1)]}
        # bids depth = 99 + 98 = $197, cap 10% → ~$19.7
        cap = compute_liquidity_cap("sell", book, max_book_fraction=0.10)
        assert 18 < cap < 21

    def test_empty_book_returns_zero(self):
        assert compute_liquidity_cap("buy", {}, max_book_fraction=0.10) == 0.0
        assert compute_liquidity_cap("sell", {"bids": [], "asks": []}, max_book_fraction=0.10) == 0.0

    def test_default_fraction_is_10pct(self):
        book = {"asks": [(100.0, 1.0)]}  # $100 depth
        cap = compute_liquidity_cap("buy", book)  # default
        assert 9 < cap < 11

    def test_invalid_side_raises(self):
        with pytest.raises(ValueError, match="side"):
            compute_liquidity_cap("hold", {"asks": [(100, 1)]})

    def test_uses_only_top_n_levels(self):
        # 10 levels but max_levels=5 → only first 5 counted
        asks = [(100.0 + i, 1.0) for i in range(10)]
        book = {"asks": asks}
        cap_5 = compute_liquidity_cap("buy", book, max_book_fraction=0.10, max_levels=5)
        cap_10 = compute_liquidity_cap("buy", book, max_book_fraction=0.10, max_levels=10)
        assert cap_10 > cap_5


class TestShouldSkipEntrySpread:
    def test_normal_spread_passes(self):
        assert should_skip_entry_spread(spread_bps=3.0, max_bps=5.0) is False

    def test_wide_spread_skips(self):
        assert should_skip_entry_spread(spread_bps=8.0, max_bps=5.0) is True

    def test_inf_spread_skips(self):
        assert should_skip_entry_spread(spread_bps=float("inf"), max_bps=5.0) is True

    def test_default_max_5bps(self):
        # Default should be conservative (~5bps for retail SPOT)
        assert should_skip_entry_spread(spread_bps=4.0) is False
        assert should_skip_entry_spread(spread_bps=10.0) is True
