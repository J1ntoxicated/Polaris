"""Marketdata-domain schema bootstrap (storage split, 2026-07-14 wipe reset).

DEMO/PAPER only. Pins:
* ``init_marketdata_db`` creates every firehose/shadow/altdata table on its
  OWN file — never touches the trading-domain tables (positions/fills/...).
* ``marketdata_db_path_for`` derives a sibling file next to the trading DB,
  honouring ``POLARIS_MARKETDATA_DB`` override.
* Idempotent re-init (boot-time re-run) does not raise.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.storage.schema_marketdata import (
    MARKETDATA_DDL,
    init_marketdata_db,
    marketdata_db_path_for,
)

_FIREHOSE_3 = ("bars", "ticker_baseline_samples", "watchlist_focus")

_EXPECTED_TABLES = (
    *_FIREHOSE_3,
    "ticker_baseline_state",
    "ticker_ground",
    "ticker_technicals",
    "quote_ticks",
    "tick_inflow",
    "altdata_snapshot",
    "gate_kill_counterfactuals",
    "entry_admission_shadow",
    "maker_fill_shadow",
    "price_through_shadow",
    "weekend_shadow_orders",
    "sector_rank_shadow",
    "vwap_timing_shadow",
    "news_timing_shadow",
    "meta_labels",
    "calibration_pairs",
    "benchmark_results",
    "edgar_filings",
    "filing_proximity_shadow",
    "stablecoin_liquidity",
    "earnings_calendar",
    "earnings_proximity_shadow",
)

_TRADING_ONLY_TABLES = (
    "positions",
    "fills",
    "signals",
    "gate_events",
    "gate_shadow_events",
    "universe",
    "regime_state",
)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {r[0] for r in rows}


def test_init_marketdata_db_creates_every_marketdata_table(tmp_path: Path) -> None:
    conn = init_marketdata_db(tmp_path / "md.sqlite")
    names = _table_names(conn)
    for table in _EXPECTED_TABLES:
        assert table in names, f"missing marketdata table: {table}"


def test_init_marketdata_db_never_creates_trading_tables(tmp_path: Path) -> None:
    conn = init_marketdata_db(tmp_path / "md.sqlite")
    names = _table_names(conn)
    for table in _TRADING_ONLY_TABLES:
        assert table not in names, f"trading table leaked into marketdata DB: {table}"


def test_init_marketdata_db_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "md.sqlite"
    init_marketdata_db(path)
    conn2 = init_marketdata_db(path)  # re-init on boot — must not raise
    assert "bars" in _table_names(conn2)


def test_marketdata_ddl_is_nonempty_and_all_create_statements() -> None:
    assert len(MARKETDATA_DDL) > 20
    for stmt in MARKETDATA_DDL:
        assert "CREATE TABLE" in stmt or "INDEX" in stmt


def test_marketdata_db_path_for_derives_sibling(tmp_path: Path) -> None:
    trading = tmp_path / "polaris_live.sqlite"
    md = marketdata_db_path_for(trading)
    assert md == tmp_path / "polaris_marketdata.sqlite"


def test_marketdata_db_path_for_env_override(
    tmp_path: Path, monkeypatch: object
) -> None:
    import os

    override = tmp_path / "custom_md.sqlite"
    os.environ["POLARIS_MARKETDATA_DB"] = str(override)
    try:
        assert marketdata_db_path_for(tmp_path / "x.sqlite") == override
    finally:
        del os.environ["POLARIS_MARKETDATA_DB"]


def test_marketdata_bars_roundtrip(tmp_path: Path) -> None:
    """Sanity: the bars table this whole split exists to unblock is writable."""
    conn = init_marketdata_db(tmp_path / "md.sqlite")
    conn.execute(
        """INSERT INTO bars
           (instrument_id, underlying_group_id, venue, symbol, bar_interval,
            ts, open, high, low, close, volume)
           VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m',
                   1000, 1.0, 2.0, 0.5, 1.5, 10.0)"""
    )
    row = conn.execute("SELECT close FROM bars WHERE instrument_id = ?",
                        ("okx:BTC-USDT",)).fetchone()
    assert row is not None
    assert row[0] == 1.5
