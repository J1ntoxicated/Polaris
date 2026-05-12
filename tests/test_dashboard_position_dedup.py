"""Tests for ``snapshot._read_positions`` logical-key dedup (B-P0-1).

Pre-fix the dashboard rendered every ``positions.status='open'`` row, so the
2026-05-10 lifecycle drift surfaced as 4 identical-symbol slots in the v2
"활성 포지션" panel. Post-fix one logical position
``(venue, symbol, strategy_id, side)`` collapses to a single ``PositionRow``
with ``row_count = N`` so the renderer can flag drift via a ``[×N]`` badge
without losing the live position itself.

Forensic note: ``vault/50_research/forensic/2026-05-10_dashboard_regression.md``.
Debate ref: ``vault/50_research/debates/2026-05-10_topic_b_dashboard_redesign.md`` §R2-2.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable

import pytest

from polaris.scripts.dashboard.snapshot import _read_positions
from polaris.storage.schema import init_db


def _seed(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str,
    symbol: str,
    strategy_id: str,
    side: str,
    qty: float,
    opened_ts: int,
    status: str = "open",
    underlying_group_id: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO positions
            (position_id, venue, symbol, underlying_group_id, strategy_id,
             entry_strategy_id, active_strategy_id, side, qty, status,
             opened_ts, swap_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (position_id, venue, symbol, underlying_group_id, strategy_id,
         strategy_id, strategy_id, side, qty, status, opened_ts),
    )


@pytest.fixture
def conn(tmp_path) -> Iterable[sqlite3.Connection]:
    db_path = tmp_path / "test.sqlite"
    c = init_db(db_path)
    try:
        yield c
    finally:
        c.close()


def _read(conn):
    return _read_positions(
        conn,
        now_s=2_000,
        last_prices={},
        entry_lookup={},
        cell_mult={},
        regime_lookup={},
    )


def test_empty_returns_empty(conn):
    assert _read(conn) == []


def test_single_open_row_yields_count_one(conn):
    _seed(conn, position_id="p1", venue="okx", symbol="BTC-USDT",
          strategy_id="tsmom", side="long", qty=0.001, opened_ts=1_000)
    rows = _read(conn)
    assert len(rows) == 1
    assert rows[0].row_count == 1
    assert rows[0].qty == pytest.approx(0.001)


def test_closed_status_ignored(conn):
    _seed(conn, position_id="pc", venue="okx", symbol="BTC-USDT",
          strategy_id="tsmom", side="long", qty=0.001, opened_ts=1_000,
          status="closed")
    assert _read(conn) == []


def test_legacy_drift_collapses_to_one_row_with_count(conn):
    """Three OPEN rows under the same logical key — should collapse to one
    PositionRow with ``row_count=3`` and ``qty`` summed."""
    for i in range(3):
        _seed(conn, position_id=f"p_legacy_{i}", venue="okx", symbol="ENJ-USDT",
              strategy_id="tsmom", side="long", qty=10.0, opened_ts=1_000 + i)
    rows = _read(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r.symbol == "ENJ-USDT"
    assert r.strategy_id == "tsmom"
    assert r.side == "long"
    assert r.row_count == 3
    assert r.qty == pytest.approx(30.0)


def test_opposite_sides_remain_separate(conn):
    """Hedge: long + short on the same (venue, symbol, strategy) → 2 rows."""
    _seed(conn, position_id="pl", venue="okx", symbol="BTC-USDT",
          strategy_id="tsmom", side="long", qty=0.001, opened_ts=1_000)
    _seed(conn, position_id="ps", venue="okx", symbol="BTC-USDT",
          strategy_id="tsmom", side="short", qty=0.002, opened_ts=1_001)
    rows = _read(conn)
    assert len(rows) == 2
    sides = sorted(r.side for r in rows)
    assert sides == ["long", "short"]
    assert all(r.row_count == 1 for r in rows)


def test_different_strategies_remain_separate(conn):
    """momentum-long + reversion-long on the same (venue, symbol, side) → 2 rows."""
    _seed(conn, position_id="pa", venue="okx", symbol="BTC-USDT",
          strategy_id="tsmom", side="long", qty=0.001, opened_ts=1_000)
    _seed(conn, position_id="pb", venue="okx", symbol="BTC-USDT",
          strategy_id="rsi_bb", side="long", qty=0.002, opened_ts=1_001)
    rows = _read(conn)
    assert len(rows) == 2
    strats = sorted(r.strategy_id for r in rows)
    assert strats == ["rsi_bb", "tsmom"]


def test_held_sec_uses_oldest_opened_ts(conn):
    """Drift collapse: held_sec must reflect the OLDEST entry (longest holding)."""
    for i in range(3):
        _seed(conn, position_id=f"q_{i}", venue="okx", symbol="SOL-USDT",
              strategy_id="tsmom", side="long", qty=1.0, opened_ts=1_500 + i)
    rows = _read(conn)
    assert len(rows) == 1
    # now_s=2000, oldest opened_ts=1500 → held_sec=500
    assert rows[0].held_sec == pytest.approx(500.0)
