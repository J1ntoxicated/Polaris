"""Tests for WhaleWall strategy (HYPO-026) — pure (P6)."""
from __future__ import annotations

import pytest

from src.domain.signal import SignalAction
from src.strategies.whale_wall import WhaleWall


def _tick(price: float = 50_000.0, ts: int = 1_000_000) -> dict:
    return {"last": price, "ts": ts}


def _book(bids: list, asks: list | None = None) -> dict:
    return {"bids": bids, "asks": asks or []}


class TestWhaleWallEntry:
    def test_enter_long_on_large_bid_wall(self):
        """Large bid within proximity → ENTER_LONG (need >= 2 bids for depth check)."""
        strat = WhaleWall(min_wall_usd=100_000, wall_proximity_pct=0.003, exit_above_wall_pct=0.015)
        # Price=50000, bid wall at 49950 (0.1% below) with $150k size
        bids = [(49_950, 3.0), (49_900, 0.5)]  # 49950 × 3 = $149,850 (wall) + small bid
        book = _book(bids)
        sig = strat.evaluate_book(_tick(50_000), book)
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.target_size_usd > 0

    def test_no_entry_wall_too_far_away(self):
        """Wall > proximity_pct below price → not detected → HOLD (insufficient book if 1 bid)."""
        strat = WhaleWall(min_wall_usd=100_000, wall_proximity_pct=0.003)
        # Bid at 49500 — 1% below (> 0.3% proximity); 1 bid = depth < 2 → HOLD
        bids = [(49_500, 3.0)]
        sig = strat.evaluate_book(_tick(50_000), _book(bids))
        # depth=1 → HOLD; even if depth>=2, wall_proximity fails → EXIT
        assert sig.action in (SignalAction.HOLD, SignalAction.EXIT)

    def test_exit_when_no_wall_with_sufficient_depth(self):
        """Enough bids but none qualify as whale wall → EXIT (support removed)."""
        strat = WhaleWall(min_wall_usd=100_000, wall_proximity_pct=0.003)
        # 3 small bids within proximity but each < $100k
        bids = [(49_990, 0.5), (49_980, 0.5), (49_970, 0.5)]  # each ~$25k
        sig = strat.evaluate_book(_tick(50_000), _book(bids))
        assert sig.action == SignalAction.EXIT

    def test_insufficient_book_returns_hold(self):
        """< 2 bids → HOLD (insufficient depth)."""
        strat = WhaleWall()
        sig = strat.evaluate_book(_tick(50_000), _book([]))
        assert sig.action == SignalAction.HOLD
        assert "insufficient" in sig.reason

    def test_wall_below_min_size_ignored(self):
        """Wall size < min_wall_usd → EXIT if depth >= 2 (no valid wall detected)."""
        strat = WhaleWall(min_wall_usd=100_000, wall_proximity_pct=0.003)
        # 3 bids within proximity but all too small
        bids = [(49_980, 1.0), (49_970, 0.5), (49_960, 0.5)]
        sig = strat.evaluate_book(_tick(50_000), _book(bids))
        assert sig.action == SignalAction.EXIT

    def test_price_far_above_wall_returns_hold(self):
        """Price far above wall (dist > exit_above_wall_pct) → HOLD (missed entry)."""
        strat = WhaleWall(min_wall_usd=100_000, wall_proximity_pct=0.02, exit_above_wall_pct=0.003)
        # price=50000, wall=49200 → dist=1.6% > exit_above_wall_pct=0.3% → HOLD (too late)
        bids = [(49_200, 3.0), (49_100, 2.0), (49_000, 1.0)]  # 49200×3=$147,600 wall
        sig = strat.evaluate_book(_tick(50_000), _book(bids))
        # dist = (50000-49200)/50000 = 1.6% > 0.3% exit_above_wall_pct → HOLD
        assert sig.action == SignalAction.HOLD

    def test_evaluate_returns_hold(self):
        """Standard evaluate() always HOLD."""
        from src.domain.candle import Candle
        strat = WhaleWall()
        c = Candle(timestamp_ms=1_000, open=100, high=110, low=90, close=105, volume=1000)
        sig = strat.evaluate([c])
        assert sig.action == SignalAction.HOLD
