"""universe.name — human-readable instrument name (DEMO/PAPER, display-only).

ADDITIVE-only ``name TEXT NOT NULL DEFAULT ''`` on ``universe``: captured at
discovery from the venue meta when present (Alpaca ``/v2/assets`` ``name``,
Capital market ``instrumentName``; OKX has none → ''). Surfaced on the dashboard
Chart selector + positions/trades rows; UI falls back to the bare symbol when ''.

Verifies:
- the column exists after init_db (fresh + legacy DB) and defaults to '',
- the discovery write-path persists ``UniverseInstrument.name`` and a later
  empty-name refresh does NOT clobber a previously captured name (sticky),
- the venue parsers extract the name field (Alpaca name / Capital instrumentName),
  and a missing field is graceful ('' — never raises),
- the Chart symbol-list read attaches the name (graceful '' for legacy DBs).

Behaviour-identical: ``name`` never feeds sizing/gating/exit. flow_not_block:
a missing name is never a drop. aggressive bias preserved — display-only column.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.universe._alpaca import _alpaca_asset_row_to_instrument
from polaris.core.universe._capital import _capital_market_row_to_instrument
from polaris.core.universe.discovery import persist_universe
from polaris.core.universe.schema import UniverseInstrument
from polaris.storage.schema import init_db
from tools.visualizer import chart_data as cd


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _coldef(conn: sqlite3.Connection, table: str, col: str) -> tuple[int, str | None]:
    for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if row[1] == col:
            return row[3], row[4]
    raise AssertionError(f"{table}.{col} missing")


def _inst(symbol: str, *, venue: str = "alpaca", name: str = "") -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"{venue}.{symbol}",
        asset_class="equity",
        quote_ccy="USD",
        state="live",
        vol_24h_usd=1_000_000.0,
        spread_bps=1.0,
        atr_24h_pct=1.0,
        depth_10bps_usd=0.0,
        last_seen_ts=1_700_000_000,
        name=name,
    )


# ── schema / migration ──────────────────────────────────────────────────────


def test_name_column_exists_and_defaults_empty(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        assert "name" in _cols(conn, "universe")
        notnull, dflt = _coldef(conn, "universe", "name")
        assert notnull == 1, "name is NOT NULL DEFAULT ''"
        assert dflt == "''", f"name default must be '' (got {dflt!r})"
    finally:
        conn.close()


def test_legacy_universe_db_gets_name_column(tmp_path: Path) -> None:
    """A legacy universe table (no name column) is ALTERed in place; existing
    rows backfill to '' without clobbering their data."""
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE universe ("
        "venue TEXT NOT NULL, symbol TEXT NOT NULL, instrument_id TEXT NOT NULL, "
        "underlying_group_id TEXT NOT NULL, asset_class TEXT NOT NULL, "
        "quote_ccy TEXT NOT NULL, state TEXT NOT NULL, vol_24h_usd REAL NOT NULL, "
        "spread_bps REAL NOT NULL, atr_24h_pct REAL NOT NULL, "
        "depth_10bps_usd REAL NOT NULL, signal_density_7d REAL NOT NULL, "
        "last_seen_ts INTEGER NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, "
        "active_reason TEXT, PRIMARY KEY (venue, symbol))"
    )
    conn.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, underlying_group_id, "
        "asset_class, quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
        "depth_10bps_usd, signal_density_7d, last_seen_ts) VALUES "
        "('okx', 'BTC-USDT', 'okx:BTC-USDT', 'okx.btc', 'crypto', 'USDT', 'live', "
        "0, 0, 0, 0, 0, 1700000000)"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        assert "name" in _cols(conn, "universe")
        row = conn.execute(
            "SELECT symbol, name FROM universe WHERE symbol = 'BTC-USDT'"
        ).fetchone()
        assert row == ("BTC-USDT", ""), "legacy row backfilled to '' (data intact)"
    finally:
        conn.close()


def test_name_migration_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "twice.sqlite"
    init_db(db).close()
    conn = init_db(db)  # re-run: guarded ALTER must not raise
    try:
        assert "name" in _cols(conn, "universe")
    finally:
        conn.close()


# ── discovery write-path ────────────────────────────────────────────────────


def test_persist_universe_writes_name(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "persist.sqlite")
    try:
        persist_universe(conn, [_inst("AAPL", name="Apple Inc.")])
        row = conn.execute(
            "SELECT name FROM universe WHERE venue='alpaca' AND symbol='AAPL'"
        ).fetchone()
        assert row[0] == "Apple Inc."
    finally:
        conn.close()


def test_empty_name_refresh_does_not_clobber(tmp_path: Path) -> None:
    """Sticky name: once captured, a later refresh carrying '' (transient meta
    fetch failure) preserves the existing name (graceful, flow_not_block)."""
    conn = init_db(tmp_path / "sticky.sqlite")
    try:
        persist_universe(conn, [_inst("AAPL", name="Apple Inc.")])
        persist_universe(conn, [_inst("AAPL", name="")])  # name fetch failed
        row = conn.execute(
            "SELECT name FROM universe WHERE venue='alpaca' AND symbol='AAPL'"
        ).fetchone()
        assert row[0] == "Apple Inc.", "empty refresh must not wipe a real name"
    finally:
        conn.close()


def test_non_empty_name_refresh_updates(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "update.sqlite")
    try:
        persist_universe(conn, [_inst("AAPL", name="Apple")])
        persist_universe(conn, [_inst("AAPL", name="Apple Inc. Common Stock")])
        row = conn.execute(
            "SELECT name FROM universe WHERE venue='alpaca' AND symbol='AAPL'"
        ).fetchone()
        assert row[0] == "Apple Inc. Common Stock"
    finally:
        conn.close()


# ── venue parsers capture the name ──────────────────────────────────────────


def test_alpaca_parser_captures_name() -> None:
    row = {
        "symbol": "AAPL",
        "class": "us_equity",
        "tradable": True,
        "status": "active",
        "exchange": "NASDAQ",
        "name": "Apple Inc. Common Stock",
    }
    inst = _alpaca_asset_row_to_instrument(row, now_ts=1_700_000_000)
    assert inst is not None
    assert inst.name == "Apple Inc. Common Stock"


def test_alpaca_parser_graceful_when_name_missing() -> None:
    row = {
        "symbol": "AAPL",
        "class": "us_equity",
        "tradable": True,
        "status": "active",
        "exchange": "NASDAQ",
    }
    inst = _alpaca_asset_row_to_instrument(row, now_ts=1_700_000_000)
    assert inst is not None
    assert inst.name == ""


def test_capital_parser_captures_instrument_name() -> None:
    row = {
        "epic": "GOLD",
        "bid": 2000.0,
        "offer": 2000.5,
        "high": 2010.0,
        "low": 1990.0,
        "instrumentType": "COMMODITIES",
        "marketStatus": "TRADEABLE",
        "instrumentName": "Gold",
    }
    inst = _capital_market_row_to_instrument(
        row, asset_class_hint="commodities", now_ts=1_700_000_000
    )
    assert inst is not None
    assert inst.name == "Gold"


def test_capital_parser_graceful_when_name_missing() -> None:
    row = {
        "epic": "GOLD",
        "bid": 2000.0,
        "offer": 2000.5,
        "high": 2010.0,
        "low": 1990.0,
        "instrumentType": "COMMODITIES",
        "marketStatus": "TRADEABLE",
    }
    inst = _capital_market_row_to_instrument(
        row, asset_class_hint="commodities", now_ts=1_700_000_000
    )
    assert inst is not None
    assert inst.name == ""


# ── Chart symbol-list read-path attaches the name ───────────────────────────


def _seed_bars(
    conn: sqlite3.Connection, *, venue: str, symbol: str, interval: str
) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bars (
            instrument_id TEXT, underlying_group_id TEXT, venue TEXT, symbol TEXT,
            bar_interval TEXT, ts INTEGER, open REAL, high REAL, low REAL,
            close REAL, volume REAL, notional_usd REAL, trade_count INTEGER,
            vwap REAL, bid_close REAL, ask_close REAL, spread_bps_close REAL,
            source TEXT)"""
    )
    conn.execute(
        "INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"{venue}:{symbol}", f"{venue}.{symbol}", venue, symbol, interval,
            1_700_000_000, 100.0, 101.0, 99.0, 100.0, 10.0, 0.0, 0, 0.0,
            0.0, 0.0, 0.0, "rest",
        ),
    )
    conn.commit()


def test_list_chart_symbols_attaches_name(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "chart.sqlite")
    try:
        _seed_bars(conn, venue="alpaca", symbol="AAPL", interval="1m")
        persist_universe(conn, [_inst("AAPL", name="Apple Inc.")])
        conn.commit()
        groups = cd.list_chart_symbols(conn)
        by_venue = {g["venue"]: g for g in groups}
        aapl = next(s for s in by_venue["alpaca"]["symbols"] if s["symbol"] == "AAPL")
        assert aapl["name"] == "Apple Inc."
    finally:
        conn.close()


def test_list_chart_symbols_graceful_no_name(tmp_path: Path) -> None:
    """A symbol with bars but no universe row gets name='' (UI shows bare sym)."""
    conn = init_db(tmp_path / "chart_noname.sqlite")
    try:
        _seed_bars(conn, venue="okx", symbol="BTC-USDT", interval="1m")
        groups = cd.list_chart_symbols(conn)
        by_venue = {g["venue"]: g for g in groups}
        btc = next(
            s for s in by_venue["okx"]["symbols"] if s["symbol"] == "BTC-USDT"
        )
        assert btc["name"] == ""
    finally:
        conn.close()


def test_chart_symbol_list_graceful_on_legacy_db_without_name_column() -> None:
    """The TRUE legacy path: a ``universe`` table with NO ``name`` column must not
    raise — the name lookup swallows the sqlite error and the selector still lists
    symbols with name='' (UI falls back to the bare symbol). Guards the graceful
    ``except sqlite3.Error`` branch that init_db would otherwise mask."""
    conn = sqlite3.connect(":memory:")
    try:
        _seed_bars(conn, venue="okx", symbol="BTC-USDT", interval="1m")
        # legacy universe table — note: NO name column.
        conn.execute(
            "CREATE TABLE universe (venue TEXT, symbol TEXT, quote_ccy TEXT, "
            "PRIMARY KEY (venue, symbol))"
        )
        conn.execute(
            "INSERT INTO universe (venue, symbol, quote_ccy) VALUES "
            "('okx', 'BTC-USDT', 'USDT')"
        )
        conn.commit()
        # must not raise despite the missing column.
        groups = cd.list_chart_symbols(conn)
        btc = next(
            s for s in groups[0]["symbols"] if s["symbol"] == "BTC-USDT"
        )
        assert btc["name"] == ""
    finally:
        conn.close()
