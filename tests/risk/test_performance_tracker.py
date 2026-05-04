"""Tests for performance_tracker — TDD (Phase 2j)."""
from __future__ import annotations

import pytest

from src.risk.performance_tracker import compute_recent_stats
from src.paper.state import Position, PositionStatus


# ── helpers ────────────────────────────────────────────────────────────────

def _pos(
    entry: float,
    exit_: float,
    size: float = 200.0,
    direction: int = 1,
    fee: float = 0.0014,
    open_ts: int = 1_000_000,
    close_ts: int = 2_000_000,
) -> Position:
    """Build a closed Position for testing."""
    p = Position(
        position_id=f"TEST-{entry}-{exit_}-{open_ts}",
        ticker="BTC-USDT",
        direction=direction,
        entry_price=entry,
        size_usd=size,
        open_ts_ms=open_ts,
        fee_round_trip=fee,
    )
    return p.close(exit_price=exit_, close_ts_ms=close_ts)


# ── unit tests ─────────────────────────────────────────────────────────────

class TestComputeRecentStats:
    def test_compute_recent_stats_basic(self) -> None:
        """10 wins, 10 losses → win_rate ~0.5."""
        positions = []
        # 10 wins: +1% gross (fee=0.14%) → net +0.86%
        for i in range(10):
            positions.append(_pos(entry=100.0, exit_=101.0, open_ts=(i + 1) * 1000, close_ts=(i + 1) * 1000 + 500))
        # 10 losses: -1% gross → net -1.14%
        for i in range(10):
            positions.append(_pos(entry=100.0, exit_=99.0, open_ts=(i + 11) * 1000, close_ts=(i + 11) * 1000 + 500))

        stats = compute_recent_stats(positions, lookback=20)
        assert stats["win_rate"] == pytest.approx(0.5, abs=0.05)
        assert stats["avg_win_pct"] > 0
        assert stats["avg_loss_pct"] > 0

    def test_cold_start_fewer_than_5(self) -> None:
        """< 5 closed positions → cold start defaults returned.
        Phase 2N: defaults updated to win=0.55, avg_win=0.8, avg_loss=0.5.
        """
        positions = [_pos(entry=100.0, exit_=101.0, open_ts=(i + 1) * 1000, close_ts=(i + 1) * 1000 + 500) for i in range(3)]
        stats = compute_recent_stats(positions, lookback=20)
        # Phase 2N cold start defaults
        assert stats["win_rate"] == pytest.approx(0.55)
        assert stats["avg_win_pct"] == pytest.approx(0.8)
        assert stats["avg_loss_pct"] == pytest.approx(0.5)

    def test_empty_positions_cold_start(self) -> None:
        # Phase 2N: cold start win_rate updated to 0.55
        stats = compute_recent_stats([], lookback=20)
        assert stats["win_rate"] == pytest.approx(0.55)

    def test_lookback_limits_to_last_n(self) -> None:
        """lookback=5 uses only last 5 positions."""
        # First 15: all losses
        positions = [_pos(entry=100.0, exit_=99.0, open_ts=(i + 1) * 1000, close_ts=(i + 1) * 1000 + 500) for i in range(15)]
        # Last 5: all wins
        for i in range(15, 20):
            positions.append(_pos(entry=100.0, exit_=101.0, open_ts=(i + 1) * 1000, close_ts=(i + 1) * 1000 + 500))

        stats = compute_recent_stats(positions, lookback=5)
        assert stats["win_rate"] == pytest.approx(1.0, abs=0.05)

    def test_all_wins(self) -> None:
        positions = [_pos(entry=100.0, exit_=102.0, open_ts=(i + 1) * 1000, close_ts=(i + 1) * 1000 + 500) for i in range(10)]
        stats = compute_recent_stats(positions, lookback=20)
        assert stats["win_rate"] == pytest.approx(1.0, abs=0.01)
        assert stats["avg_loss_pct"] == 0  # no losses

    def test_all_losses(self) -> None:
        positions = [_pos(entry=100.0, exit_=98.0, open_ts=(i + 1) * 1000, close_ts=(i + 1) * 1000 + 500) for i in range(10)]
        stats = compute_recent_stats(positions, lookback=20)
        assert stats["win_rate"] == pytest.approx(0.0, abs=0.01)
        assert stats["avg_win_pct"] == 0  # no wins

    def test_win_detection_accounts_for_fee(self) -> None:
        """A trade with +0.05% gross but fee=0.14% round-trip should be a LOSS."""
        # gross 0.0005, fee 0.0014 → net negative → loss
        positions = [
            _pos(entry=100.0, exit_=100.05, fee=0.0014,
                 open_ts=(i + 1) * 1000, close_ts=(i + 1) * 1000 + 500)
            for i in range(10)
        ]
        stats = compute_recent_stats(positions, lookback=20)
        # All net-negative → 0% win rate
        assert stats["win_rate"] == pytest.approx(0.0, abs=0.01)
