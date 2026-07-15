"""Dashboard cross-domain reads post storage-split (2026-07-14 wipe reset).

DEMO/PAPER, display-only. Confirms the specific cross-domain audit item
(polaris_graph.py:earnings_calendar JOIN universe) resolves via read-only
ATTACH of the marketdata sibling file, and that the pure marketdata reads
(_query_stablecoin_series) resolve against the marketdata sibling — never
the trading db_path itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.storage.schema import ALL_DDL
from polaris.storage.schema_marketdata import marketdata_db_path_for
from tools.visualizer import polaris_graph as pg


def _make_split_dbs(tmp_path: Path) -> Path:
    """Trading db (universe) + its marketdata sibling (earnings_calendar,
    stablecoin_liquidity) — both built from the (unchanged) full-superset
    ``ALL_DDL`` so either file alone can also stand in for single-DB tests.
    """
    db_path = tmp_path / "polaris_live.sqlite"
    conn = sqlite3.connect(db_path)
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    conn.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, "
        "underlying_group_id, asset_class, quote_ccy, state, last_seen_ts) "
        "VALUES ('alpaca', 'JPM', 'alpaca:JPM', 'equity:JPM', 'equity', "
        "'USD', 'live', 1000)"
    )
    conn.commit()
    conn.close()
    md_path = marketdata_db_path_for(db_path)
    md_conn = sqlite3.connect(md_path)
    for stmt in ALL_DDL:
        md_conn.executescript(stmt)
    md_conn.execute(
        "INSERT INTO earnings_calendar "
        "(symbol, earnings_date, hour, eps_estimate, ingestion_ts, created_ts) "
        "VALUES ('JPM', date('now', '+2 days'), 'bmo', 2.5, 1000, 1000)"
    )
    md_conn.execute(
        "INSERT INTO stablecoin_liquidity (ts, symbol, mcap_usd, created_ts) "
        "VALUES (1000, 'TOTAL', 150000000000.0, 1000)"
    )
    md_conn.commit()
    md_conn.close()
    return db_path


def test_upcoming_earnings_reads_marketdata_sibling_via_universe_join(
    tmp_path: Path,
) -> None:
    db_path = _make_split_dbs(tmp_path)
    pg._upcoming_earnings_cache["ts"] = 0.0
    rows = pg._query_upcoming_earnings(db_path)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "JPM"


def test_upcoming_earnings_missing_marketdata_file_degrades_empty(
    tmp_path: Path,
) -> None:
    # Trading db exists (with universe) but the marketdata sibling never
    # booted — must degrade to an empty panel, never raise.
    db_path = tmp_path / "polaris_live.sqlite"
    conn = sqlite3.connect(db_path)
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    conn.commit()
    conn.close()
    pg._upcoming_earnings_cache["ts"] = 0.0
    assert pg._query_upcoming_earnings(db_path) == []


def test_stablecoin_series_reads_marketdata_sibling(tmp_path: Path) -> None:
    db_path = _make_split_dbs(tmp_path)
    pg._stable_series_cache["ts"] = 0.0
    rows = pg._query_stablecoin_series(db_path)
    assert len(rows) == 1
    assert rows[0]["total"] == 150000000000.0


def test_stablecoin_series_ignores_stale_trading_file_copy(
    tmp_path: Path,
) -> None:
    """A row written only to the TRADING file's (unused, superset-schema)
    stablecoin_liquidity copy must NOT surface — the real data lives in the
    marketdata sibling only."""
    db_path = tmp_path / "polaris_live.sqlite"
    conn = sqlite3.connect(db_path)
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    conn.execute(
        "INSERT INTO stablecoin_liquidity (ts, symbol, mcap_usd, created_ts) "
        "VALUES (999, 'TOTAL', 1.0, 999)"
    )
    conn.commit()
    conn.close()
    pg._stable_series_cache["ts"] = 0.0
    assert pg._query_stablecoin_series(db_path) == []
