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
    deal_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO positions
            (position_id, venue, symbol, underlying_group_id, strategy_id,
             entry_strategy_id, active_strategy_id, side, qty, status,
             opened_ts, swap_count, deal_id)
        VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (position_id, venue, symbol, strategy_id, strategy_id, strategy_id,
         side, qty, status, opened_ts, deal_id),
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
    order_id: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO fills
            (fill_id, venue, instrument_id, strategy_id, side, size_usd,
             fill_price, fee_usd, slippage_bps, ts_ms, order_id,
             contribution_id, pnl_usd, is_close, base_qty, quote_qty, state)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, 0, 0, ?, ?, 'filled')
        """,
        (fill_id, venue, instrument_id, strategy_id, side, size_usd,
         fill_price, ts_ms, order_id, contribution_id,
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


def test_capital_deal_id_hydrated_from_positions_column(conn):
    """A Capital (cfd) position persisted with deal_id → hydrate restores it.

    positions.deal_id is the SSOT for the Capital close key.
    """
    _seed_position(conn, position_id="pos_cap", venue="capital", symbol="GOLD",
                   strategy_id="xau", side="long", qty=1.0, opened_ts=1000,
                   deal_id="DIAAAAAA-1234-5678")
    _seed_entry_fill(conn, fill_id="f_cap", venue="capital",
                     instrument_id="capital:GOLD", strategy_id="xau",
                     side="long", size_usd=100.0, fill_price=2000.0,
                     contribution_id="pos_cap", ts_ms=1_000_000,
                     order_id="DIAAAAAA-1234-5678")

    trades = hydrate_open_positions(conn)
    assert len(trades) == 1
    assert trades[0].deal_id == "DIAAAAAA-1234-5678"


def test_capital_restart_reaches_close_path_with_deal_id(conn):
    """THE key test: a Capital position persisted with a deal_id, then
    re-hydrated via the restore path (simulating a restart), has trade.deal_id
    set → the close path's Capital branch is reached with a NON-None deal_id
    (no '[real-close] Capital close needs ... deal_id=None' error)."""
    persisted_deal_id = "DIAAAAAA-DEAD-BEEF"
    _seed_position(conn, position_id="pos_restart", venue="capital",
                   symbol="EURUSD", strategy_id="fx", side="short", qty=2.0,
                   opened_ts=2000, deal_id=persisted_deal_id)
    _seed_entry_fill(conn, fill_id="f_restart", venue="capital",
                     instrument_id="capital:EURUSD", strategy_id="fx",
                     side="short", size_usd=200.0, fill_price=1.08,
                     contribution_id="pos_restart", ts_ms=2_000_000,
                     order_id=persisted_deal_id)

    # simulate restart: in-memory state is empty; rebuild from SQLite only.
    [trade] = hydrate_open_positions(conn)
    # The close path's Capital branch guards on `not trade.deal_id`; a
    # non-None deal_id means it is reached and closes by deal_id.
    assert trade.venue == "capital"
    assert trade.deal_id is not None
    assert trade.deal_id == persisted_deal_id


def test_okx_hydrate_leaves_deal_id_none(conn):
    """OKX closes by base_qty — deal_id stays None even if a column exists."""
    _seed_position(conn, position_id="pos_okx", venue="okx", symbol="BTC-USDT",
                   strategy_id="tsmom", side="long", qty=0.001, opened_ts=1000,
                   deal_id=None)
    _seed_entry_fill(conn, fill_id="f_okx", venue="okx",
                     instrument_id="okx:BTC-USDT", strategy_id="tsmom",
                     side="long", size_usd=50.0, fill_price=50_000.0,
                     contribution_id="pos_okx", ts_ms=1_000_000,
                     order_id="okx-ord-123")
    [trade] = hydrate_open_positions(conn)
    assert trade.deal_id is None


def test_capital_legacy_falls_back_to_fill_order_id(conn):
    """Legacy Capital row (positions.deal_id NULL) falls back to the entry
    fill's order_id stash so pre-column DBs still close on restart."""
    _seed_position(conn, position_id="pos_legacy_cap", venue="capital",
                   symbol="SILVER", strategy_id="xau", side="long", qty=1.0,
                   opened_ts=1000, deal_id=None)
    _seed_entry_fill(conn, fill_id="f_legacy_cap", venue="capital",
                     instrument_id="capital:SILVER", strategy_id="xau",
                     side="long", size_usd=100.0, fill_price=25.0,
                     contribution_id="pos_legacy_cap", ts_ms=1_000_000,
                     order_id="DIAAAAAA-LEGACY-01")
    [trade] = hydrate_open_positions(conn)
    assert trade.deal_id == "DIAAAAAA-LEGACY-01"
