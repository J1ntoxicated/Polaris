"""3-stream architecture P0-1 schema migration tests.

ADDITIVE-only columns ``product_class`` + ``stream_id`` on ``universe`` and
``positions`` (stream_architecture_redesign §2.3). Verifies:
- new columns exist with '' default after init_db,
- idempotent ALTER (re-running init_db on the same DB is safe),
- a legacy DB (table created before the columns existed) gets ALTER + backfill,
- backfill maps okx→spot/A_okx_crypto, capital→cfd/B_capital_cfd.

DEMO/PAPER only. Behaviour-identical: columns default to '' so existing reads
are unaffected.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.storage.schema import _apply_post_migrations, init_db

STREAM_COLS = ("product_class", "stream_id")


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _coldef(conn: sqlite3.Connection, table: str, col: str) -> tuple[int, str | None]:
    """Return (notnull, dflt_value) for a column from PRAGMA table_info."""
    for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if row[1] == col:
            return row[3], row[4]
    raise AssertionError(f"{table}.{col} missing")


def test_new_columns_exist_on_fresh_db(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        for table in ("universe", "positions"):
            cols = _cols(conn, table)
            for col in STREAM_COLS:
                assert col in cols, f"{table}.{col} missing on fresh init"
    finally:
        conn.close()


def test_new_columns_default_empty_string(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "default.sqlite")
    try:
        for table in ("universe", "positions"):
            for col in STREAM_COLS:
                notnull, dflt = _coldef(conn, table, col)
                assert notnull == 1, f"{table}.{col} must be NOT NULL"
                assert dflt == "''", f"{table}.{col} default must be '' (got {dflt!r})"
    finally:
        conn.close()


def test_idempotent_reinit_is_safe(tmp_path: Path) -> None:
    db = tmp_path / "reinit.sqlite"
    init_db(db).close()
    # Re-running must not raise (duplicate-column ALTER guarded by table_info).
    conn = init_db(db)
    try:
        for table in ("universe", "positions"):
            cols = _cols(conn, table)
            for col in STREAM_COLS:
                assert col in cols
    finally:
        conn.close()


def test_idempotent_apply_post_migrations_twice(tmp_path: Path) -> None:
    db = tmp_path / "twice.sqlite"
    conn = init_db(db)
    try:
        # Directly invoking the helper a second time must be a no-op, not raise.
        _apply_post_migrations(conn)
        _apply_post_migrations(conn)
    finally:
        conn.close()


def test_legacy_db_gets_alter_and_backfill(tmp_path: Path) -> None:
    """A DB whose universe/positions predate the stream columns must be
    ALTERed in place and existing rows backfilled by venue."""
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db))
    # Legacy CREATE TABLE without product_class/stream_id.
    conn.execute(
        "CREATE TABLE universe ("
        "venue TEXT NOT NULL, symbol TEXT NOT NULL, instrument_id TEXT NOT NULL, "
        "underlying_group_id TEXT NOT NULL, asset_class TEXT NOT NULL, "
        "quote_ccy TEXT NOT NULL, state TEXT NOT NULL, last_seen_ts INTEGER NOT NULL, "
        "is_active INTEGER NOT NULL DEFAULT 1, "
        "PRIMARY KEY (venue, symbol))"
    )
    conn.execute(
        "CREATE TABLE positions ("
        "position_id TEXT PRIMARY KEY, venue TEXT NOT NULL, symbol TEXT NOT NULL, "
        "strategy_id TEXT NOT NULL, entry_strategy_id TEXT NOT NULL, "
        "active_strategy_id TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, "
        "status TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, underlying_group_id, "
        "asset_class, quote_ccy, state, last_seen_ts) VALUES "
        "('okx', 'BTC-USDT', 'BTC-USDT', 'crypto:BTC', 'crypto', 'USDT', 'live', 0), "
        "('capital', 'GOLD', 'GOLD', 'commodity:XAU', 'commodity', 'USD', 'live', 0)"
    )
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status) VALUES "
        "('p1', 'okx', 'BTC-USDT', 's1', 's1', 's1', 'long', 1.0, 'open'), "
        "('p2', 'capital', 'GOLD', 's2', 's2', 's2', 'long', 1.0, 'open')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        for table in ("universe", "positions"):
            assert STREAM_COLS[0] in _cols(conn, table)
            assert STREAM_COLS[1] in _cols(conn, table)

        u = dict(
            conn.execute(
                "SELECT venue, product_class FROM universe ORDER BY venue"
            ).fetchall()
        )
        assert u == {"capital": "cfd", "okx": "spot"}
        us = dict(
            conn.execute(
                "SELECT venue, stream_id FROM universe ORDER BY venue"
            ).fetchall()
        )
        assert us == {"capital": "B_capital_cfd", "okx": "A_okx_crypto"}

        p = dict(
            conn.execute(
                "SELECT venue, product_class FROM positions ORDER BY venue"
            ).fetchall()
        )
        assert p == {"capital": "cfd", "okx": "spot"}
        ps = dict(
            conn.execute(
                "SELECT venue, stream_id FROM positions ORDER BY venue"
            ).fetchall()
        )
        assert ps == {"capital": "B_capital_cfd", "okx": "A_okx_crypto"}
    finally:
        conn.close()


def test_backfill_does_not_clobber_existing_values(tmp_path: Path) -> None:
    """Backfill only touches blank rows; an already-set value is preserved."""
    db = tmp_path / "preserve.sqlite"
    conn = init_db(db)
    try:
        conn.execute(
            "INSERT INTO universe (venue, symbol, instrument_id, "
            "underlying_group_id, asset_class, product_class, stream_id, "
            "quote_ccy, state, last_seen_ts) VALUES "
            "('okx', 'ETH-USDT', 'ETH-USDT', 'crypto:ETH', 'crypto', "
            "'manual_pc', 'manual_sid', 'USDT', 'live', 0)"
        )
        conn.commit()
        _apply_post_migrations(conn)
        row = conn.execute(
            "SELECT product_class, stream_id FROM universe WHERE symbol = 'ETH-USDT'"
        ).fetchone()
        assert row == ("manual_pc", "manual_sid")
    finally:
        conn.close()
