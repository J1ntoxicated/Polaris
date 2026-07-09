"""Graveyard-sweep migration — archive (dump+gzip) then DROP market_events/orders.

DEMO/PAPER. Targets a LIVE DB snapshot pre-dating the DDL removal (this repo's
own ``init_db()`` no longer creates these tables — see
``polaris/storage/schema.py`` graveyard-sweep). Tests build the two tables by
hand to simulate that pre-existing production schema.

DRY-RUN is the default (no DB mutation, no archive write); ``--execute`` is
required to actually archive+drop. Archival is unconditional before any DROP
so both the git diff and the gzip dump preserve the data (double
preservation) — ``orders`` is archived for its DDL even though it holds 0 rows.
"""

from __future__ import annotations

import gzip
import sqlite3
from pathlib import Path

import pytest

from tools.migrate_graveyard_sweep_2026_07_10 import (
    TABLES,
    archive_tables,
    drop_tables,
    main,
    table_exists,
    table_row_count,
)


def _seed_legacy_db(db_path: Path) -> None:
    """Build a pre-DDL-removal DB: market_events (rows) + orders (0 rows)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE market_events (ts INTEGER NOT NULL, type TEXT NOT NULL, "
        "venue TEXT NOT NULL, symbol TEXT NOT NULL, payload_json TEXT NOT NULL, "
        "PRIMARY KEY (ts, type, venue, symbol))"
    )
    conn.execute(
        "CREATE TABLE orders (order_id TEXT PRIMARY KEY, venue TEXT NOT NULL, "
        "symbol TEXT NOT NULL, strategy_id TEXT NOT NULL, order_key TEXT NOT NULL, "
        "status TEXT NOT NULL, payload_hash TEXT NOT NULL, "
        "created_ts INTEGER NOT NULL DEFAULT 0, updated_ts INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_orders_strategy_order_key ON orders(strategy_id, order_key)"
    )
    conn.executemany(
        "INSERT INTO market_events (ts, type, venue, symbol, payload_json) "
        "VALUES (?, 'regime_flip', 'okx', ?, '{}')",
        [(1_700_000_000 + i, f"G{i}") for i in range(3)],
    )
    # A second unrelated table must survive the sweep untouched.
    conn.execute("CREATE TABLE positions (position_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO positions VALUES ('p1')")
    conn.commit()
    conn.close()


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "legacy.sqlite"
    _seed_legacy_db(db_path)
    return db_path


def test_tables_constant_is_market_events_and_orders() -> None:
    assert TABLES == ("market_events", "orders")


def test_table_row_count_and_exists(legacy_db: Path) -> None:
    conn = sqlite3.connect(legacy_db)
    try:
        assert table_exists(conn, "market_events") is True
        assert table_exists(conn, "orders") is True
        assert table_exists(conn, "no_such_table") is False
        assert table_row_count(conn, "market_events") == 3
        assert table_row_count(conn, "orders") == 0
    finally:
        conn.close()


def test_archive_tables_preserves_ddl_and_rows(legacy_db: Path, tmp_path: Path) -> None:
    conn = sqlite3.connect(legacy_db)
    archive_dir = tmp_path / "archive"
    try:
        out_path = archive_tables(conn, TABLES, archive_dir, date_str="2026-07-10")
    finally:
        conn.close()

    assert out_path.exists()
    assert out_path.name == "graveyard_sweep_2026-07-10.sql.gz"
    text = gzip.decompress(out_path.read_bytes()).decode("utf-8")

    # DDL for BOTH tables preserved, including the 0-row orders table.
    assert "CREATE TABLE market_events" in text
    assert "CREATE TABLE orders" in text
    assert "idx_orders_strategy_order_key" in text
    # All 3 market_events rows dumped as INSERTs; orders contributes none.
    assert text.count("INSERT INTO market_events") == 3
    assert "INSERT INTO orders" not in text
    # Unrelated table never touched by the archive.
    assert "positions" not in text


def test_drop_tables_removes_both(legacy_db: Path) -> None:
    conn = sqlite3.connect(legacy_db)
    try:
        drop_tables(conn, TABLES)
        assert table_exists(conn, "market_events") is False
        assert table_exists(conn, "orders") is False
        # Unrelated table survives.
        assert table_exists(conn, "positions") is True
    finally:
        conn.close()


def test_drop_tables_idempotent_when_already_dropped(legacy_db: Path) -> None:
    conn = sqlite3.connect(legacy_db)
    try:
        drop_tables(conn, TABLES)
        drop_tables(conn, TABLES)  # second call must not raise
        assert table_exists(conn, "market_events") is False
    finally:
        conn.close()


def test_main_dry_run_makes_no_changes(legacy_db: Path, tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    main(["--db", str(legacy_db), "--archive-dir", str(archive_dir)])

    conn = sqlite3.connect(legacy_db)
    try:
        assert table_exists(conn, "market_events") is True
        assert table_exists(conn, "orders") is True
    finally:
        conn.close()
    assert not archive_dir.exists() or list(archive_dir.iterdir()) == []


def test_main_execute_archives_then_drops(legacy_db: Path, tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    main([
        "--db", str(legacy_db),
        "--archive-dir", str(archive_dir),
        "--execute",
        "--date", "2026-07-10",
    ])

    conn = sqlite3.connect(legacy_db)
    try:
        assert table_exists(conn, "market_events") is False
        assert table_exists(conn, "orders") is False
    finally:
        conn.close()

    dumps = list(archive_dir.glob("graveyard_sweep_*.sql.gz"))
    assert len(dumps) == 1
    text = gzip.decompress(dumps[0].read_bytes()).decode("utf-8")
    assert text.count("INSERT INTO market_events") == 3


def test_main_execute_on_missing_tables_is_a_noop(tmp_path: Path) -> None:
    """A DB that never had these tables (already migrated) must not crash."""
    db_path = tmp_path / "already_migrated.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE positions (position_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    archive_dir = tmp_path / "archive"
    main(["--db", str(db_path), "--archive-dir", str(archive_dir), "--execute"])

    conn = sqlite3.connect(db_path)
    try:
        assert table_exists(conn, "positions") is True
    finally:
        conn.close()
