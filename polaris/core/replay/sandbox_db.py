"""In-memory sandbox SQLite seeded read-only from the live DB.

This is the **only** module that touches the live DB, and it does so strictly
read-only: the live file is opened with ``mode=ro`` URI, ``ATTACH``-ed, and the
learner/cell/regime snapshot tables are copied row-for-row into a throwaway
``:memory:`` connection via ``INSERT ... SELECT``. The replay engine's
``update_on_trade_close`` writes land **only** on this in-memory conn, so the
live ``cell_stats`` rows are immutable by construction (TDD #6 isolation).

Each walk-forward window can re-seed an independent sandbox, giving each split a
pristine snapshot.
"""

from __future__ import annotations

import contextlib
import sqlite3
from typing import Final

__all__ = [
    "SEED_TABLES",
    "seed_sandbox",
]

# Snapshot tables the sizing/exit read seams consult + the cell-matrix write
# seam upserts. Copied verbatim so the replay sees the same learner/cell/regime
# state the live loop saw at snapshot time. Bars are NOT copied here — the
# engine reads bars from the read-only ATTACH directly (large, append-only).
SEED_TABLES: Final[tuple[str, ...]] = (
    "cell_matrix_p0",
    "cell_matrix_parent3",
    "cell_matrix_parent2",
    "cell_matrix_shadow_context",
    "regime_state",
    "learner_state",
    "learner_blocks",
    "learner_posterior",
    "strategy_regime_prior",
    # gross-LCB SCALE-gate read seam (fee_split_flip_r2_2026-07-12) — G5 sizing
    # (compute_size -> resolve_gross_lcb) reads this on the SAME conn it's
    # handed, so the sandbox must carry it too or every replay position-open
    # crashes with "no such table" the instant sizing consults it.
    "score_f_events",
)


def _live_ro_uri(live_db_path: str) -> str:
    return f"file:{live_db_path}?mode=ro"


def seed_sandbox(live_db_path: str, *, tables: tuple[str, ...] = SEED_TABLES) -> sqlite3.Connection:
    """Build a fresh ``:memory:`` conn seeded from ``live_db_path`` (read-only).

    The live DB is ATTACH-ed read-only; each seed table's DDL is read from the
    live ``sqlite_master`` (so columns match exactly), recreated in memory, and
    populated with ``INSERT INTO main.<t> SELECT * FROM live.<t>``. Tables absent
    in the live DB are skipped (a fresh live DB may not have every table yet).
    Returns the in-memory conn; the caller owns its lifetime.
    """
    # autocommit (isolation_level=None) so update_on_trade_close's explicit
    # BEGIN IMMEDIATE / COMMIT runs without an implicit outer transaction.
    mem = sqlite3.connect(":memory:", isolation_level=None)
    mem.execute("ATTACH DATABASE ? AS live", (_live_ro_uri(live_db_path),))
    for table in tables:
        ddl_row = mem.execute(
            "SELECT sql FROM live.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if ddl_row is None or ddl_row[0] is None:
            continue  # table not present in this live DB — skip (cold start)
        mem.executescript(ddl_row[0])
        mem.execute(f"INSERT INTO main.{table} SELECT * FROM live.{table}")
        # Recreate any indices the table depends on (cell-matrix/learner reads
        # use them; harmless if absent).
        for idx_row in mem.execute(
            "SELECT sql FROM live.sqlite_master WHERE type='index' AND tbl_name=? "
            "AND sql IS NOT NULL",
            (table,),
        ).fetchall():
            # auto-index / already created — non-fatal
            with contextlib.suppress(sqlite3.OperationalError):
                mem.executescript(idx_row[0])
    mem.commit()
    # Detach so the in-memory conn no longer holds the live file open. Writes
    # from here on are :memory:-only — the live DB is never reachable again.
    mem.execute("DETACH DATABASE live")
    return mem
