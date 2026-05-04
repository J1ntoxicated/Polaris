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
        """n >= 10 + win < 40% → fast_fail."""
        closed = [_winner()] * 3 + [_loser()] * 8  # 3/11 = 27% win
        reason = check_deprecate("TEST", closed, self.START_MS, now_ms=self.NOW_MS)
        assert reason is not None
        assert "fast_fail" in reason

    def test_no_trigger_above_40pct(self):
        """n >= 10 + win >= 40% → no fast_fail trigger."""
        closed = [_winner()] * 5 + [_loser()] * 5  # 50% win
        reason = check_deprecate("TEST", closed, self.START_MS, now_ms=self.NOW_MS)
        assert reason is None  # 50% win + realized > -$10

    def test_loss_cap_trigger(self):
        """Realized < -$10 → loss_cap."""
        # Each loser: size=200, net=-0.0214 → -$4.28 per trade
        # 3 losers = -$12.84 → triggers
        closed = [_loser(size_usd=200.0)] * 3
        reason = check_deprecate("TEST", closed, self.START_MS, now_ms=self.NOW_MS)
        assert reason is not None
        assert "loss_cap" in reason

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
        """4 winners + 5 losers (but n<10) → no fast_fail; realized may cause loss_cap."""
        closed = [_winner()] * 4 + [_loser()] * 4  # n=8 < 10, no fast_fail check
        # realized: 4 winners × $2.72 - 4 losers × $4.28 = $10.88 - $17.12 = -$6.24 > -$10
        start_ms = self.NOW_MS - 12 * 3_600_000
        reason = check_deprecate("TEST", closed, start_ms, now_ms=self.NOW_MS)
        assert reason is None  # n<10 no fast_fail; loss > -$10; < 24h


class TestCheckAllHypos:
    def test_batch_identifies_deprecated(self):
        """Batch check returns correct HYPO to deprecate."""
        hypos = [
            {"hypo_id": "GOOD"},
            {"hypo_id": "BAD"},
        ]
        now_ms = 100 * 3_600_000
        start_ms = now_ms - 25 * 3_600_000

        def get_closed(hid: str):
            if hid == "BAD":
                return [_loser(size_usd=200.0)] * 3  # loss_cap
            return []

        def get_started(hid: str):
            return start_ms

        result = check_all_hypos(hypos, get_closed, get_started, now_ms=now_ms)
        ids = [r[0] for r in result]
        # BAD has loss_cap; GOOD has frequency trigger (25h, n=0)
        assert "BAD" in ids
