"""Tick-stream decouple — quote_ticks single-row LWW schema + migration.

Design SSOT: vault/50_research/debates/tick_stream_decouple_2026-06-24.md.

quote_ticks collapses from PK=(instrument_id, ts) (append-per-tick → unbounded
645k-row/215MB growth + writer↔writer DELETE lock contention) to a single-row
last-write-wins table PK=instrument_id. The dashboard / Sentinel-S1 read only
the latest-per-instrument row, so LWW is byte-equivalent for them while the
table can no longer grow.

These tests pin:
1. a FRESH init_db creates quote_ticks with PK=instrument_id (single-row LWW);
2. INSERT OR REPLACE collapses many ts for one instrument to ONE latest row;
3. the additive-first migration rebuilds a legacy (instrument_id, ts)-PK table
   to single-row LWW, keeping the LATEST ts per instrument, and is idempotent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.storage.schema import init_db

NOW = 1_900_000_000


def _quote_ticks_pk_columns(conn: sqlite3.Connection) -> list[str]:
    """Return the ordered PK column names of quote_ticks."""
    rows = conn.execute("PRAGMA table_info(quote_ticks)").fetchall()
    # row = (cid, name, type, notnull, dflt_value, pk_index); pk_index>0 ⇒ PK.
    pk = sorted((r[5], r[1]) for r in rows if r[5])
    return [name for _idx, name in pk]


def test_fresh_schema_quote_ticks_pk_is_instrument_only() -> None:
    conn = init_db(":memory:")
    try:
        assert _quote_ticks_pk_columns(conn) == ["instrument_id"]
    finally:
        conn.close()


def test_insert_or_replace_collapses_many_ts_to_one_row(tmp_path: Path) -> None:
    db = tmp_path / "lww.sqlite"
    conn = init_db(db)
    try:
        for ts in (NOW, NOW + 1, NOW + 2):
            conn.execute(
                "INSERT OR REPLACE INTO quote_ticks "
                "(instrument_id, venue, symbol, ts, bid, ask, mid, spread_bps) "
                "VALUES ('okx:BTC-USDT','okx','BTC-USDT',?,1,1,?,1)",
                (ts, float(ts)),
            )
        conn.commit()
        rows = conn.execute(
            "SELECT instrument_id, ts, mid FROM quote_ticks"
        ).fetchall()
        assert rows == [("okx:BTC-USDT", NOW + 2, float(NOW + 2))]
    finally:
        conn.close()


def _make_legacy_quote_ticks(conn: sqlite3.Connection) -> None:
    """Create the OLD append-per-tick quote_ticks (PK=(instrument_id, ts))."""
    conn.execute(
        """CREATE TABLE quote_ticks (
            instrument_id TEXT NOT NULL,
            venue TEXT NOT NULL,
            symbol TEXT NOT NULL,
            ts INTEGER NOT NULL,
            bid REAL NOT NULL,
            ask REAL NOT NULL,
            mid REAL NOT NULL,
            spread_bps REAL NOT NULL,
            bid_size REAL NOT NULL DEFAULT 0.0,
            ask_size REAL NOT NULL DEFAULT 0.0,
            last_trade_price REAL NOT NULL DEFAULT 0.0,
            last_trade_size REAL NOT NULL DEFAULT 0.0,
            source TEXT NOT NULL DEFAULT 'rest',
            PRIMARY KEY (instrument_id, ts)
        )"""
    )


def test_migration_rebuilds_legacy_to_single_row_lww(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db)
    _make_legacy_quote_ticks(conn)
    # Two instruments, each with several appended ticks at increasing ts.
    for inst, venue in (("okx:BTC-USDT", "okx"), ("capital:US100", "capital")):
        for ts, mid in ((NOW, 1.0), (NOW + 5, 2.0), (NOW + 9, 3.0)):
            conn.execute(
                "INSERT INTO quote_ticks "
                "(instrument_id, venue, symbol, ts, bid, ask, mid, spread_bps) "
                "VALUES (?,?,?,?,1,1,?,1)",
                (inst, venue, inst.split(":")[1], ts, mid),
            )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM quote_ticks").fetchone()[0] == 6
    conn.close()

    # init_db runs the additive-first migration.
    conn = init_db(db)
    try:
        assert _quote_ticks_pk_columns(conn) == ["instrument_id"]
        rows = conn.execute(
            "SELECT instrument_id, ts, mid FROM quote_ticks ORDER BY instrument_id"
        ).fetchall()
        # One row per instrument = the LATEST (max ts) tick.
        assert rows == [
            ("capital:US100", NOW + 9, 3.0),
            ("okx:BTC-USDT", NOW + 9, 3.0),
        ]
    finally:
        conn.close()

    # Idempotent: a second init_db is a no-op (already single-row LWW).
    conn = init_db(db)
    try:
        assert _quote_ticks_pk_columns(conn) == ["instrument_id"]
        assert conn.execute("SELECT COUNT(*) FROM quote_ticks").fetchone()[0] == 2
    finally:
        conn.close()
