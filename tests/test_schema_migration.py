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


def test_deal_id_column_exists_on_fresh_db(tmp_path: Path) -> None:
    """positions.deal_id (Capital close key SSOT) present on a fresh init."""
    conn = init_db(tmp_path / "deal_fresh.sqlite")
    try:
        assert "deal_id" in _cols(conn, "positions")
        notnull, dflt = _coldef(conn, "positions", "deal_id")
        assert notnull == 0, "deal_id must be nullable (OKX leaves it NULL)"
        assert dflt is None, f"deal_id default must be NULL (got {dflt!r})"
    finally:
        conn.close()


def test_deal_id_legacy_db_gets_alter_idempotent(tmp_path: Path) -> None:
    """A legacy positions table (no deal_id) is ALTERed in place; re-running
    init_db / _apply_post_migrations is safe (duplicate-column ALTER guarded)."""
    db = tmp_path / "deal_legacy.sqlite"
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
        "('p_cap', 'capital', 'GOLD', 's1', 's1', 's1', 'long', 1.0, 'open')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        assert "deal_id" in _cols(conn, "positions"), "legacy DB missing deal_id"
        # legacy row backfills to NULL (no deal_id known retroactively).
        row = conn.execute(
            "SELECT deal_id FROM positions WHERE position_id = 'p_cap'"
        ).fetchone()
        assert row[0] is None
        # re-running migrations must not raise (duplicate-column ALTER guarded).
        _apply_post_migrations(conn)
        _apply_post_migrations(conn)
        assert "deal_id" in _cols(conn, "positions")
    finally:
        conn.close()


def test_entry_regime_column_exists_on_fresh_db(tmp_path: Path) -> None:
    """positions.entry_regime (adaptive thesis re-map anchor) present + nullable
    on a fresh init."""
    conn = init_db(tmp_path / "regime_fresh.sqlite")
    try:
        assert "entry_regime" in _cols(conn, "positions")
        notnull, dflt = _coldef(conn, "positions", "entry_regime")
        assert notnull == 0, "entry_regime must be nullable (legacy rows NULL)"
        assert dflt is None, f"entry_regime default must be NULL (got {dflt!r})"
    finally:
        conn.close()


def test_entry_regime_legacy_db_gets_alter_idempotent(tmp_path: Path) -> None:
    """A legacy positions table (no entry_regime) is ALTERed in place; legacy rows
    backfill to NULL; re-running migrations is safe (duplicate-column guarded)."""
    db = tmp_path / "regime_legacy.sqlite"
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
        "('p_leg', 'okx', 'BTC-USDT', 's1', 's1', 's1', 'long', 1.0, 'open')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        assert "entry_regime" in _cols(conn, "positions"), "missing entry_regime"
        row = conn.execute(
            "SELECT entry_regime FROM positions WHERE position_id = 'p_leg'"
        ).fetchone()
        assert row[0] is None, "legacy row must backfill entry_regime to NULL"
        _apply_post_migrations(conn)
        _apply_post_migrations(conn)
        assert "entry_regime" in _cols(conn, "positions")
    finally:
        conn.close()


def test_entry_regime_v2_column_exists_on_fresh_db(tmp_path: Path) -> None:
    """positions.entry_regime_v2 (regime v2 twinlight SHADOW, backgate-plan
    W2-d) present + nullable on a fresh init — bare alongside entry_regime
    (구 4라벨), which stays the sole canonical behavior key."""
    conn = init_db(tmp_path / "regime_v2_fresh.sqlite")
    try:
        assert "entry_regime_v2" in _cols(conn, "positions")
        notnull, dflt = _coldef(conn, "positions", "entry_regime_v2")
        assert notnull == 0, "entry_regime_v2 must be nullable"
        assert dflt is None, f"entry_regime_v2 default must be NULL (got {dflt!r})"
    finally:
        conn.close()


def test_entry_regime_v2_legacy_db_gets_alter_idempotent(tmp_path: Path) -> None:
    """A legacy positions table (no entry_regime_v2) is ALTERed in place;
    legacy rows backfill to NULL; re-running migrations is safe."""
    db = tmp_path / "regime_v2_legacy.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE positions ("
        "position_id TEXT PRIMARY KEY, venue TEXT NOT NULL, symbol TEXT NOT NULL, "
        "strategy_id TEXT NOT NULL, entry_strategy_id TEXT NOT NULL, "
        "active_strategy_id TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, "
        "status TEXT NOT NULL, entry_regime TEXT)"
    )
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, entry_regime) "
        "VALUES ('p_leg', 'okx', 'BTC-USDT', 's1', 's1', 's1', 'long', 1.0, "
        "'open', 'chop')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        assert "entry_regime_v2" in _cols(conn, "positions"), "missing entry_regime_v2"
        row = conn.execute(
            "SELECT entry_regime, entry_regime_v2 FROM positions "
            "WHERE position_id = 'p_leg'"
        ).fetchone()
        assert row[0] == "chop", "구 4라벨 entry_regime must survive untouched"
        assert row[1] is None, "legacy row must backfill entry_regime_v2 to NULL"
        _apply_post_migrations(conn)
        _apply_post_migrations(conn)
        assert "entry_regime_v2" in _cols(conn, "positions")
    finally:
        conn.close()


def test_regime_v2_column_exists_on_fresh_regime_state(tmp_path: Path) -> None:
    """regime_state.regime_v2 (backgate-plan W2-d) present + nullable on a
    fresh init — bare alongside regime (구 4라벨, unchanged canonical key)."""
    conn = init_db(tmp_path / "regime_state_fresh.sqlite")
    try:
        cols = _cols(conn, "regime_state")
        assert "regime_v2" in cols
        assert "regime" in cols
        notnull, dflt = _coldef(conn, "regime_state", "regime_v2")
        assert notnull == 0, "regime_v2 must be nullable"
        assert dflt is None, f"regime_v2 default must be NULL (got {dflt!r})"
    finally:
        conn.close()


def test_regime_v2_legacy_regime_state_gets_alter_idempotent(tmp_path: Path) -> None:
    """A legacy regime_state table (no regime_v2) is ALTERed in place; the
    구 4라벨 regime column and its value survive untouched."""
    db = tmp_path / "regime_state_legacy.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE regime_state ("
        "venue TEXT NOT NULL, underlying_group_id TEXT NOT NULL, "
        "regime TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.0, "
        "updated_ts INTEGER NOT NULL, "
        "PRIMARY KEY (venue, underlying_group_id))"
    )
    conn.execute(
        "INSERT INTO regime_state (venue, underlying_group_id, regime, "
        "confidence, updated_ts) VALUES ('okx', 'BTC', 'bull', 0.8, 1000)"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        cols = _cols(conn, "regime_state")
        assert "regime_v2" in cols, "missing regime_v2 after migrate"
        row = conn.execute(
            "SELECT regime, regime_v2 FROM regime_state "
            "WHERE venue = 'okx' AND underlying_group_id = 'BTC'"
        ).fetchone()
        assert row[0] == "bull", "구 4라벨 regime must survive untouched"
        assert row[1] is None, "legacy row must backfill regime_v2 to NULL"
        _apply_post_migrations(conn)
        _apply_post_migrations(conn)
        assert "regime_v2" in _cols(conn, "regime_state")
    finally:
        conn.close()


def test_close_path_byte_identical_with_regime_v2_twinlight(tmp_path: Path) -> None:
    """behavior-0 regression: an existing OPEN position's close path (status
    -> 'closed', pnl_r / entry_regime writes) is byte-identical with the
    regime_v2 twinlight columns present — the migration adds a NULL-default
    column only, it never threads into the close write path."""
    conn = init_db(tmp_path / "close_regress.sqlite")
    try:
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
            " entry_strategy_id, active_strategy_id, side, qty, status, "
            " entry_regime) VALUES ('p_close', 'okx', 'BTC-USDT', 's1', 's1', "
            " 's1', 'long', 1.0, 'open', 'crisis')"
        )
        conn.commit()
        # Existing close path: status flip + pnl_r write, exactly as the live
        # close writer does — entry_regime_v2 is never referenced.
        conn.execute(
            "UPDATE positions SET status = 'closed', closed_ts = 2000, "
            "pnl_r = 0.42 WHERE position_id = 'p_close'"
        )
        conn.commit()
        row = conn.execute(
            "SELECT status, pnl_r, entry_regime, entry_regime_v2 FROM positions "
            "WHERE position_id = 'p_close'"
        ).fetchone()
        assert row[0] == "closed"
        assert abs(row[1] - 0.42) < 1e-9
        assert row[2] == "crisis", "구 4라벨 entry_regime unaffected by close"
        assert row[3] is None, "entry_regime_v2 stays NULL — no close-path write"
    finally:
        conn.close()


def test_legacy_segments_gets_cell_key_and_index(tmp_path: Path) -> None:
    """A legacy position_strategy_segments table (no cell_key/lineage cols) must
    migrate WITHOUT crashing — the cell_key index is created in
    _apply_post_migrations AFTER the ALTER, never in ALL_DDL (which would run the
    CREATE INDEX before the column exists and abort startup on any existing DB)."""
    db = tmp_path / "legacy_seg.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE position_strategy_segments ("
        "segment_id TEXT PRIMARY KEY, position_id TEXT NOT NULL, "
        "strategy_id TEXT NOT NULL, regime_at_start TEXT, "
        "started_ts INTEGER, ended_ts INTEGER, exit_reason TEXT, pnl_r REAL)"
    )
    conn.execute(
        "INSERT INTO position_strategy_segments "
        "(segment_id, position_id, strategy_id, started_ts) "
        "VALUES ('seg_legacy', 'p_old', 's1', 1000)"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)  # must not raise OperationalError: no such column: cell_key
    try:
        cols = _cols(conn, "position_strategy_segments")
        for col in ("cell_key", "venue", "ticker", "pnl_usd", "trade_id"):
            assert col in cols, f"legacy segments missing {col} after migrate"
        idx = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='position_strategy_segments'"
            ).fetchall()
        }
        assert any("cell" in i for i in idx), "cell_key index not created post-migrate"
        # legacy row backfills the new NOT NULL columns to their defaults.
        row = conn.execute(
            "SELECT cell_key, venue, ticker, pnl_usd FROM "
            "position_strategy_segments WHERE segment_id = 'seg_legacy'"
        ).fetchone()
        assert row == ("", "", "", 0.0)
    finally:
        conn.close()
