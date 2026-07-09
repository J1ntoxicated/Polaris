"""Graveyard sweep — archive (dump+gzip) then DROP ``market_events`` / ``orders``.

DEMO/PAPER. Both tables are reader-0/writer-0 orphans as of this sweep:

- ``market_events`` — 43k+ rows, written only by
  ``regime_flip.py::_publish_event`` (removed in this same sweep; the live
  ``strategy_swap`` / ``entrance_leans`` regime consumers read ``regime_state``
  instead, untouched), ~3,200 INSERT/day WAL single-writer contention, zero
  SELECT readers anywhere in the codebase.
- ``orders`` — 0 rows, writer never built; ``fills`` absorbed its role. The
  last reader (``dashboard_v0.py``'s orders-fallback) was deleted in this same
  sweep.

DRY-RUN is the default — reports row counts and exits without touching the
DB. ``--execute`` is required to actually archive+drop. The archive (DDL +
every row, gzip-compressed) is written to disk BEFORE the DROP runs, so the
data survives even if the DROP step is later needed for forensics — ``orders``
is archived for its DDL/index snapshot alone even though it holds 0 rows.

This script only ever touches the DB PATH it is given (``--db``). It never
runs against a live bot process — the actual DROP against
``data/polaris_live.sqlite`` is a separate, explicit step performed during a
bot stop window (out of scope here; see the graveyard-sweep branch notes).

Usage
-----
    python3 -m tools.migrate_graveyard_sweep_2026_07_10 --db data/polaris_live.sqlite
    python3 -m tools.migrate_graveyard_sweep_2026_07_10 --db data/polaris_live.sqlite --execute
"""

from __future__ import annotations

import argparse
import gzip
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("migrate_graveyard_sweep")

TABLES: tuple[str, ...] = ("market_events", "orders")


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def table_row_count(conn: sqlite3.Connection, table: str) -> int:
    if not table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def _dump_table_sql(conn: sqlite3.Connection, table: str) -> list[str]:
    """CREATE TABLE + CREATE INDEX + one INSERT per row — mirrors ``.dump``.

    Always emits the DDL (even for a 0-row table, e.g. ``orders``) so the
    schema snapshot is preserved regardless of row count.
    """
    if not table_exists(conn, table):
        return [f"-- table {table} did not exist at sweep time"]
    lines: list[str] = []
    ddl_rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name = ? AND sql IS NOT NULL "
        "AND type IN ('table', 'index') ORDER BY (type = 'index')",
        (table,),
    ).fetchall()
    lines.extend(f"{sql};" for (sql,) in ddl_rows)
    cur = conn.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    col_list = ", ".join(cols)
    for row in cur.fetchall():
        values = ", ".join(_sql_literal(v) for v in row)
        lines.append(f"INSERT INTO {table} ({col_list}) VALUES({values});")
    return lines


def archive_tables(
    conn: sqlite3.Connection,
    tables: tuple[str, ...],
    archive_dir: Path,
    *,
    date_str: str | None = None,
) -> Path:
    """Dump ``tables`` (DDL + rows) to a single gzip file under ``archive_dir``."""
    date_str = date_str or datetime.now(UTC).strftime("%Y-%m-%d")
    archive_dir.mkdir(parents=True, exist_ok=True)
    out_path = archive_dir / f"graveyard_sweep_{date_str}.sql.gz"
    body: list[str] = []
    for table in tables:
        body.append(f"-- {table}")
        body.extend(_dump_table_sql(conn, table))
        body.append("")
    payload = "\n".join(body).encode("utf-8")
    out_path.write_bytes(gzip.compress(payload))
    return out_path


def drop_tables(conn: sqlite3.Connection, tables: tuple[str, ...]) -> None:
    """DROP each table if present. Idempotent — a re-run on an already-swept
    DB is a no-op (no error)."""
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Archive (dump+gzip) then DROP market_events/orders."
    )
    parser.add_argument("--db", required=True, help="path to the target sqlite DB")
    parser.add_argument(
        "--archive-dir", default="data/archive", help="directory for the gzip dump"
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="archive + DROP the tables (default: dry-run report only)",
    )
    parser.add_argument("--date", default=None, help="override archive filename date (tests)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    conn = sqlite3.connect(args.db, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        counts = {t: table_row_count(conn, t) for t in TABLES}
        present = {t: table_exists(conn, t) for t in TABLES}
        for t in TABLES:
            if present[t]:
                logger.info("[graveyard] %s: %d row(s)", t, counts[t])
            else:
                logger.info("[graveyard] %s: already absent — nothing to sweep", t)

        if not any(present.values()):
            logger.info("[graveyard] nothing to do — both tables already dropped")
            return 0

        if not args.execute:
            logger.info(
                "[graveyard] DRY-RUN — would archive to %s then DROP %s "
                "(pass --execute to apply)",
                args.archive_dir, ", ".join(t for t in TABLES if present[t]),
            )
            return 0

        archive_path = archive_tables(
            conn, TABLES, Path(args.archive_dir), date_str=args.date
        )
        logger.info("[graveyard] archived → %s", archive_path)
        drop_tables(conn, TABLES)
        logger.info("[graveyard] DROPPED: %s", ", ".join(t for t in TABLES if present[t]))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
