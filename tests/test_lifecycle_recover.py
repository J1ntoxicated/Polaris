"""Tests for ``polaris.core.lifecycle.recover.hydrate_open_positions``.

Session-hydrate restores ``state.open_trades`` from persisted DB OPEN rows
so paper-loop restart does not strand previous-session positions. Drives
A-PR1 of the 2026-05-10 lifecycle-fix debate (forensic note
``2026-05-10_position_lifecycle_drift.md``).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable

import pytest

from polaris.core.lifecycle.recover import hydrate_open_positions
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.storage.schema import init_db


def _seed_position(
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
) -> None:
    conn.execute(
        """
        INSERT INTO positions
            (position_id, venue, symbol, underlying_group_id, strategy_id,
             entry_strategy_id, active_strategy_id, side, qty, status,
             opened_ts, swap_count)
        VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (position_id, venue, symbol, strategy_id, strategy_id, strategy_id,
         side, qty, status, opened_ts),
    )


def _seed_entry_fill(
    conn: sqlite3.Connection,
    *,
    fill_id: str,
    venue: str,
    instrument_id: str,
    strategy_id: str,
    side: str,
    size_usd: float,
    fill_price: float,
    contribution_id: str,
    ts_ms: int,
) -> None:
    conn.execute(
        """
        INSERT INTO fills
            (fill_id, venue, instrument_id, strategy_id, side, size_usd,
             fill_price, fee_usd, slippage_bps, ts_ms, order_id,
             contribution_id, pnl_usd, is_close, base_qty, quote_qty, state)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, '', ?, 0, 0, ?, ?, 'filled')
        """,
        (fill_id, venue, instrument_id, strategy_id, side, size_usd,
         fill_price, ts_ms, contribution_id,
         size_usd / fill_price if fill_price > 0 else 0.0, size_usd),
    )


@pytest.fixture
def conn(tmp_path) -> Iterable[sqlite3.Connection]:
    db_path = tmp_path / "test.sqlite"
    c = init_db(db_path)
    try:
        yield c
    finally:
        c.close()


def test_empty_returns_empty(conn):
    assert hydrate_open_positions(conn) == []


def test_single_open_hydrates_full_simulated_trade(conn):
    _seed_position(conn, position_id="pos_1", venue="okx", symbol="BTC-USDT",
                   strategy_id="tsmom", side="long", qty=0.001, opened_ts=1000)
    _seed_entry_fill(conn, fill_id="f_1", venue="okx",
                     instrument_id="okx:BTC-USDT", strategy_id="tsmom",
                     side="long", size_usd=50.0, fill_price=50_000.0,
                     contribution_id="pos_1", ts_ms=1_000_000)

    trades = hydrate_open_positions(conn)
    assert len(trades) == 1
    t = trades[0]
    assert isinstance(t, SimulatedTrade)
    assert t.position_id == "pos_1"
    assert t.venue == "okx"
    assert t.symbol == "BTC-USDT"
    assert t.strategy_id == "tsmom"
    assert t.side == "long"
    assert t.entry_price == 50_000.0
    assert t.notional_usd == pytest.approx(50.0)
    assert t.open_ts == 1000
    assert t.closed is False


def test_closed_status_ignored(conn):
    _seed_position(conn, position_id="pos_c", venue="okx", symbol="BTC-USDT",
                   strategy_id="tsmom", side="long", qty=0.001, opened_ts=1000,
                   status="closed")
    _seed_entry_fill(conn, fill_id="f_c", venue="okx",
                     instrument_id="okx:BTC-USDT", strategy_id="tsmom",
                     side="long", size_usd=50.0, fill_price=50_000.0,
                     contribution_id="pos_c", ts_ms=1_000_000)
    assert hydrate_open_positions(conn) == []


def test_multiple_legacy_drift_rows_all_surfaced(conn):
    """Same logical key with 3 legacy OPEN rows — hydrate must surface all 3.

    Dedup is migration's job (A-PR2), not hydrate's. Hydrate must be
    transparent about persisted state so later passes can decide.
    """
    for i in range(3):
        pid = f"pos_legacy_{i}"
        _seed_position(conn, position_id=pid, venue="okx", symbol="ENJ-USDT",
                       strategy_id="tsmom", side="long", qty=10.0,
                       opened_ts=1000 + i)
        _seed_entry_fill(conn, fill_id=f"f_{i}", venue="okx",
                         instrument_id="okx:ENJ-USDT", strategy_id="tsmom",
                         side="long", size_usd=5.0, fill_price=0.5,
                         contribution_id=pid, ts_ms=1_000_000 + i)

    trades = hydrate_open_positions(conn)
    assert len(trades) == 3
    assert sorted(t.position_id for t in trades) == [
        "pos_legacy_0", "pos_legacy_1", "pos_legacy_2"
    ]


def test_open_without_entry_fill_skipped(conn):
    """OPEN row stranded with no entry fill — skip (close path needs entry_price)."""
    _seed_position(conn, position_id="pos_orphan", venue="okx",
                   symbol="BTC-USDT", strategy_id="tsmom", side="long",
                   qty=0.001, opened_ts=1000)
    assert hydrate_open_positions(conn) == []


def test_close_fill_not_treated_as_entry(conn):
    """is_close=1 fill must not satisfy the entry-fill join."""
    _seed_position(conn, position_id="pos_x", venue="okx", symbol="BTC-USDT",
                   strategy_id="tsmom", side="long", qty=0.001, opened_ts=1000)
    # close fill exists, no open fill
    conn.execute(
        """
        INSERT INTO fills
            (fill_id, venue, instrument_id, strategy_id, side, size_usd,
             fill_price, fee_usd, slippage_bps, ts_ms, order_id,
             contribution_id, pnl_usd, is_close, base_qty, quote_qty, state)
        VALUES ('f_close', 'okx', 'okx:BTC-USDT', 'tsmom', 'long', 50.0,
                51000.0, 0, 0, 1500000, '', 'pos_x', 10.0, 1, 0.001, 50.0,
                'filled')
        """
    )
    assert hydrate_open_positions(conn) == []


def test_scale_in_multiple_entry_fills_weighted_avg(conn):
    """Same position_id with 2 entry fills — A-PR4 scale-in scenario.

    entry_price = SUM(fill_price * size_usd) / SUM(size_usd) (weighted avg
    so PnL denominator in close path matches actual cost basis).
    notional_usd = SUM(size_usd).
    """
    _seed_position(conn, position_id="pos_si", venue="okx", symbol="BTC-USDT",
                   strategy_id="tsmom", side="long", qty=0.002, opened_ts=1000)
    _seed_entry_fill(conn, fill_id="f_si_1", venue="okx",
                     instrument_id="okx:BTC-USDT", strategy_id="tsmom",
                     side="long", size_usd=50.0, fill_price=50_000.0,
                     contribution_id="pos_si", ts_ms=1_000_000)
    _seed_entry_fill(conn, fill_id="f_si_2", venue="okx",
                     instrument_id="okx:BTC-USDT", strategy_id="tsmom",
                     side="long", size_usd=50.0, fill_price=51_000.0,
                     contribution_id="pos_si", ts_ms=1_000_500)

    trades = hydrate_open_positions(conn)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == pytest.approx(50_500.0)
    assert t.notional_usd == pytest.approx(100.0)


def test_orders_oldest_first(conn):
    _seed_position(conn, position_id="pos_b", venue="okx", symbol="BTC-USDT",
                   strategy_id="tsmom", side="long", qty=0.001, opened_ts=2000)
    _seed_position(conn, position_id="pos_a", venue="okx", symbol="ETH-USDT",
                   strategy_id="tsmom", side="long", qty=0.01, opened_ts=1000)
    _seed_entry_fill(conn, fill_id="fb", venue="okx",
                     instrument_id="okx:BTC-USDT", strategy_id="tsmom",
                     side="long", size_usd=50.0, fill_price=50_000.0,
                     contribution_id="pos_b", ts_ms=2_000_000)
    _seed_entry_fill(conn, fill_id="fa", venue="okx",
                     instrument_id="okx:ETH-USDT", strategy_id="tsmom",
                     side="long", size_usd=30.0, fill_price=3_000.0,
                     contribution_id="pos_a", ts_ms=1_000_000)
    trades = hydrate_open_positions(conn)
    assert [t.position_id for t in trades] == ["pos_a", "pos_b"]
