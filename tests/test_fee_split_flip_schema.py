"""Schema tests for fee-split v1 FLIP additive DDL (fee_drag_bps column +
score_f_remap_table). DEMO/PAPER only. ADDITIVE ONLY — mirrors the v0
schema test's own contract (test_fee_split_v0_schema.py)."""
from __future__ import annotations

import sqlite3

from polaris.storage.schema import ALL_DDL, init_db


def test_ddl_alone_has_fee_drag_bps_column():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(score_f_events)")}
    assert "fee_drag_bps" in cols


def test_ddl_alone_has_remap_table():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(score_f_remap_table)")}
    assert {
        "scope_key", "scope_type", "n_samples", "computed_ts",
        "schmitt_bench_partial", "schmitt_bench_full", "schmitt_prove",
        "prove_stagnation", "ladder_step0", "ladder_step1", "ladder_step2",
        "ladder_decay", "ref_population_json", "stale", "psi", "ks",
    }.issubset(cols)


def test_init_db_migration_backfills_fee_drag_bps_on_legacy_db(tmp_path):
    """A pre-flip DB file (created before fee_drag_bps existed) gets the
    column added via the ALTER-guard migration on the next init_db() call —
    same backfill-guard precedent as the v0 gross_usd/notional_usd/
    fee_raw_usd trio."""
    db_path = tmp_path / "legacy.sqlite"
    legacy_conn = sqlite3.connect(str(db_path), isolation_level=None)
    for stmt in ALL_DDL:
        legacy_conn.execute(stmt)
    legacy_conn.execute("ALTER TABLE score_f_events DROP COLUMN fee_drag_bps")
    legacy_conn.close()

    migrated = init_db(db_path)
    cols = {row[1] for row in migrated.execute("PRAGMA table_info(score_f_events)")}
    assert "fee_drag_bps" in cols
