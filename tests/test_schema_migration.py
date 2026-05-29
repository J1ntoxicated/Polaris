"""BUILD_SCHEMA — positions excursion / precise-exit additive columns.

ADDITIVE-only columns on ``positions`` (precise-exit #26 prerequisite):
``stop_price`` / ``peak_price`` / ``trough_price`` / ``mfe_r`` / ``mae_r``
(all REAL DEFAULT NULL) + ``exit_state`` (TEXT DEFAULT 'open'). Verifies:
- new columns exist after init_db (fresh + legacy DB),
- REAL columns default NULL, exit_state defaults 'open',
- idempotent ALTER (re-running init_db / _apply_post_migrations is safe),
- a legacy DB (table created before the columns existed) gets ALTER + the
  open-row exit_state backfill, without clobbering existing data.

DEMO/PAPER only. Behaviour-identical: nullable columns so existing reads
are unaffected. Measurement only — never gates sizing, never blocks entry.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.storage.schema import _apply_post_migrations, init_db

EXCURSION_COLS = ("stop_price", "peak_price", "trough_price", "mfe_r", "mae_r")
ALL_NEW_COLS = (*EXCURSION_COLS, "exit_state")


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _coldef(conn: sqlite3.Connection, table: str, col: str) -> tuple[int, str | None]:
    """Return (notnull, dflt_value) for a column from PRAGMA table_info."""
    for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if row[1] == col:
            return row[3], row[4]
    raise AssertionError(f"{table}.{col} missing")


def test_excursion_columns_exist_on_fresh_db(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "fresh.sqlite")
    try:
        cols = _cols(conn, "positions")
        for col in ALL_NEW_COLS:
            assert col in cols, f"positions.{col} missing on fresh init"
    finally:
        conn.close()


def test_real_columns_default_null(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "default.sqlite")
    try:
        for col in EXCURSION_COLS:
            notnull, _dflt = _coldef(conn, "positions", col)
            assert notnull == 0, f"positions.{col} must be nullable"
    finally:
        conn.close()


def test_exit_state_defaults_open(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "exit_state.sqlite")
    try:
        notnull, dflt = _coldef(conn, "positions", "exit_state")
        assert notnull == 0, "exit_state must be nullable (DEFAULT 'open')"
        assert dflt == "'open'", f"exit_state default must be 'open' (got {dflt!r})"
    finally:
        conn.close()


def test_new_row_gets_open_default(tmp_path: Path) -> None:
    """A position inserted without exit_state inherits the 'open' default."""
    conn = init_db(tmp_path / "newrow.sqlite")
    try:
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
            " entry_strategy_id, active_strategy_id, side, qty, status) "
            "VALUES ('p1', 'okx', 'BTC-USDT', 's1', 's1', 's1', 'long', 1.0, 'open')"
        )
        row = conn.execute(
            "SELECT stop_price, peak_price, trough_price, mfe_r, mae_r, exit_state "
            "FROM positions WHERE position_id = 'p1'"
        ).fetchone()
        assert row[:5] == (None, None, None, None, None)
        assert row[5] == "open"
    finally:
        conn.close()


def test_idempotent_reinit_is_safe(tmp_path: Path) -> None:
    db = tmp_path / "reinit.sqlite"
    init_db(db).close()
    conn = init_db(db)  # re-run: duplicate-column ALTER must be guarded
    try:
        cols = _cols(conn, "positions")
        for col in ALL_NEW_COLS:
            assert col in cols
    finally:
        conn.close()


def test_idempotent_apply_post_migrations_twice(tmp_path: Path) -> None:
    db = tmp_path / "twice.sqlite"
    conn = init_db(db)
    try:
        _apply_post_migrations(conn)
        _apply_post_migrations(conn)
        cols = _cols(conn, "positions")
        for col in ALL_NEW_COLS:
            assert col in cols
    finally:
        conn.close()


def test_legacy_db_gets_alter_and_open_backfill(tmp_path: Path) -> None:
    """A legacy positions table (no excursion columns) is ALTERed in place and
    open rows get exit_state='open'; closed rows stay NULL."""
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE positions ("
        "position_id TEXT PRIMARY KEY, venue TEXT NOT NULL, symbol TEXT NOT NULL, "
        "strategy_id TEXT NOT NULL, entry_strategy_id TEXT NOT NULL, "
        "active_strategy_id TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, "
        "status TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status) VALUES "
        "('p_open', 'okx', 'BTC-USDT', 's1', 's1', 's1', 'long', 1.0, 'open'), "
        "('p_closed', 'okx', 'ETH-USDT', 's2', 's2', 's2', 'long', 1.0, 'closed')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        cols = _cols(conn, "positions")
        for col in ALL_NEW_COLS:
            assert col in cols, f"legacy DB missing positions.{col} after migrate"
        states = dict(
            conn.execute("SELECT position_id, exit_state FROM positions").fetchall()
        )
        assert states["p_open"] == "open", "open row must backfill exit_state='open'"
        assert states["p_closed"] is None, "closed row must stay NULL (not FSM-driven)"
        # Excursion REAL columns remain NULL for legacy rows.
        row = conn.execute(
            "SELECT stop_price, peak_price, trough_price, mfe_r, mae_r "
            "FROM positions WHERE position_id = 'p_open'"
        ).fetchone()
        assert row == (None, None, None, None, None)
    finally:
        conn.close()


def test_backfill_does_not_clobber_existing_exit_state(tmp_path: Path) -> None:
    """exit_state backfill only touches NULL open rows; a set value survives."""
    conn = init_db(tmp_path / "preserve.sqlite")
    try:
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
            " entry_strategy_id, active_strategy_id, side, qty, status, exit_state) "
            "VALUES ('p1', 'okx', 'BTC-USDT', 's1', 's1', 's1', 'long', 1.0, "
            " 'open', 'harvest')"
        )
        conn.commit()
        _apply_post_migrations(conn)
        row = conn.execute(
            "SELECT exit_state FROM positions WHERE position_id = 'p1'"
        ).fetchone()
        assert row[0] == "harvest"
    finally:
        conn.close()
