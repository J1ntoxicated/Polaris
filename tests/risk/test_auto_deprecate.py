"""Tests for auto_deprecate module — pure (P6)."""
from __future__ import annotations

import pytest

from src.risk.auto_deprecate import check_deprecate, check_all_hypos, _is_win, _realized_usd


class FakePos:
    """Minimal Position-like object for testing."""

    def __init__(
        self,
        direction: int = 1,
        entry_price: float = 100.0,
        exit_price: float = 101.0,
        fee_round_trip: float = 0.0014,
        size_usd: float = 200.0,
    ):
        self.direction = direction
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.fee_round_trip = fee_round_trip
        self.size_usd = size_usd


def _winner(size_usd: float = 200.0) -> FakePos:
    """Net positive trade."""
    return FakePos(direction=1, entry_price=100.0, exit_price=101.5, fee_round_trip=0.0014, size_usd=size_usd)


def _loser(size_usd: float = 200.0) -> FakePos:
    """Net negative trade."""
    return FakePos(direction=1, entry_price=100.0, exit_price=98.0, fee_round_trip=0.0014, size_usd=size_usd)


class TestIsWin:
    def test_winner_identified(self):
        assert _is_win(_winner()) is True

    def test_loser_identified(self):
        assert _is_win(_loser()) is False

    def test_zero_exit_price(self):
        pos = FakePos(exit_price=0.0)
        assert _is_win(pos) is False


class TestRealizedUsd:
    def test_single_winner(self):
        pos = _winner(size_usd=200.0)
        # gross = 1.5/100 = 0.015, net = 0.015 - 0.0014 = 0.0136
        r = _realized_usd([pos])
        assert abs(r - 200 * 0.0136) < 0.01

    def test_single_loser(self):
        pos = _loser(size_usd=200.0)
        # gross = -2/100 = -0.02, net = -0.02 - 0.0014 = -0.0214
        r = _realized_usd([pos])
        assert r < 0


class TestCheckDeprecate:
    NOW_MS = 100 * 3_600_000  # 100 hours in ms (well past 24h)
    START_MS = NOW_MS - 25 * 3_600_000  # 25h ago

    def test_fast_fail_trigger(self):
        """n >= 20 + win < 40% → fast_fail (Phase 5: 5 → 20, Bailey 2014).

        21 trades, 6 wins = 28.6% win rate < 40% → triggers.
        """
        closed = [_winner()] * 6 + [_loser()] * 15  # 6/21 = 28.6% win
        reason = check_deprecate("TEST", closed, self.START_MS, now_ms=self.NOW_MS)
        assert reason is not None
        assert "fast_fail" in reason

    def test_no_fast_fail_below_min_n(self):
        """n < 20 → no fast_fail even with low win rate (Phase 5 min_n=20).

        10 trades, 0 wins = 0% win — below min_n=20, no fast_fail.
        May trigger loss_cap — use winners to avoid it.
        """
        closed = [_winner()] * 5 + [_loser()] * 5  # n=10 < 20
        # 5 winners: each net = (101.5-100)/100 - 0.0014 = 0.0136, $200 = +$2.72 each
        # 5 losers: each net = (98-100)/100 - 0.0014 = -0.0214, $200 = -$4.28 each
        # total = 5*2.72 - 5*4.28 = 13.60 - 21.40 = -7.80 → > -$15 (no loss_cap)
        reason = check_deprecate("TEST", closed, self.START_MS, now_ms=self.NOW_MS)
        # n=10 < 20 → no fast_fail; realized -$7.80 > -$15 → no loss_cap; > 24h but n>=3 → no freq
        assert reason is None

    def test_no_trigger_above_40pct(self):
        """n >= 20 + win >= 40% → no fast_fail trigger.

        Phase 5: min_n=20. Use 12 winners + 8 losers = 60% win, net positive.
        """
        closed = [_winner()] * 12 + [_loser()] * 8  # 12/20 = 60% win — above 40%
        reason = check_deprecate("TEST", closed, self.START_MS, now_ms=self.NOW_MS)
        assert reason is None  # 60% win + realized positive

    def test_loss_cap_trigger(self):
        """Realized < -$15 → loss_cap (Phase 5: -$5 → -$15).

        Each loser: size=200, net=-0.0214 → -$4.28 per trade.
        4 losers = -$17.12 → triggers (< -$15).
        """
        closed = [_loser(size_usd=200.0)] * 4
        reason = check_deprecate("TEST", closed, self.START_MS, now_ms=self.NOW_MS)
        assert reason is not None
        assert "loss_cap" in reason

    def test_loss_cap_not_triggered_at_minus_14(self):
        """Realized > -$15 → no loss_cap (Phase 5 threshold).

        3 losers = -$12.84 → does NOT trigger (> -$15).
        n=3 < 20 → no fast_fail. 25h elapsed, n=3 >= 3 → no frequency trigger.
        """
        closed = [_loser(size_usd=200.0)] * 3  # -$12.84 (> -$15)
        reason = check_deprecate("TEST", closed, self.START_MS, now_ms=self.NOW_MS)
        assert reason is None

    def test_frequency_trigger_24h_no_trades(self):
        """25h elapsed + n < 3 → frequency trigger."""
        reason = check_deprecate("TEST", [], self.START_MS, now_ms=self.NOW_MS)
        assert reason is not None
        assert "frequency" in reason

    def test_no_frequency_trigger_under_24h(self):
        """< 24h elapsed → no frequency trigger even with 0 trades."""
        start_ms = self.NOW_MS - 12 * 3_600_000  # only 12h ago
        reason = check_deprecate("TEST", [], start_ms, now_ms=self.NOW_MS)
        assert reason is None

    def test_no_trigger_with_enough_trades_and_above_40pct(self):
        """n<20 → no fast_fail; small loss → no loss_cap; < 24h → no frequency.

        Phase 5: min_n=20, max_loss_usd=-$15.
        Use 2 winners + 1 loser: n=3 < 20 (no fast_fail), realized > -$15 (no loss_cap), < 24h (no freq).
        """
        closed = [_winner()] * 2 + [_loser()] * 1  # n=3 < 20, realized net positive
        start_ms = self.NOW_MS - 12 * 3_600_000
        reason = check_deprecate("TEST", closed, start_ms, now_ms=self.NOW_MS)
        assert reason is None  # n<20 no fast_fail; net positive (no loss_cap); < 24h


class TestCheckAllHypos:
    def test_batch_identifies_deprecated(self):
        """Batch check returns correct HYPO to deprecate (Phase 5: loss_cap -$15).

        BAD: 4 losers × $200 = -$17.12 → triggers loss_cap < -$15.
        GOOD: 0 trades → frequency trigger (25h, n=0 < 3).
        """
        hypos = [
            {"hypo_id": "GOOD"},
            {"hypo_id": "BAD"},
        ]
        now_ms = 100 * 3_600_000
        start_ms = now_ms - 25 * 3_600_000

        def get_closed(hid: str):
            if hid == "BAD":
                return [_loser(size_usd=200.0)] * 4  # 4 × -$4.28 = -$17.12 < -$15 → loss_cap
            return []

        def get_started(hid: str):
            return start_ms

        result = check_all_hypos(hypos, get_closed, get_started, now_ms=now_ms)
        ids = [r[0] for r in result]
        # BAD has loss_cap; GOOD has frequency trigger (25h, n=0)
        assert "BAD" in ids
