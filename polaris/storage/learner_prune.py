"""Learner/cell dead-strategy prune (#13) — DEMO/PAPER; data-integrity cleanup.

When a strategy is KILLed (un-registered from ``STRATEGY_REGISTRY`` — e.g.
``volume_burst`` #61, ``fx_range_fade`` wave1) its already-written learner +
cell-matrix rows linger. The live pipeline never reads them (it only queries
keys for REGISTERED strategies), so they cause no crash, but they DO pollute the
strategy-level parent2/parent3 roll-ups and every observability/dashboard read.

This module removes ONLY the rows whose ``strategy_id`` is absent from the
supplied live set. Live-strategy warming (the honest, post-measurement-fix cell
n_eff / pnl_r) is preserved — this is a surgical PRUNE, not a blind reset. It
changes NO trading behaviour:

  - flow_not_block: nothing that flows through sizing/gating is removed — dead
    strategies don't flow. Aggressive bias / the -1.0R rail / the 5/20 learner
    thresholds / EWMA / ADR-006 are untouched; only orphaned DATA is deleted.
  - The immutable ``fills`` ledger is NEVER touched (audit truth preserved,
    matching the measurement_reset.py philosophy: raw history is referenceable).

Idempotent (a second run deletes 0 rows) and degrade-never-crash (a missing
table is skipped, an empty live set is REFUSED so a registry-import failure can
never wipe the whole matrix). Applied as a one-shot migration by ops at deploy;
the running loop then re-warms forward against the current 21-strategy registry.

``learner_snapshot`` (per-learner ``payload_json`` rollback archives) is left
out of scope on purpose: a rollback to a pre-prune snapshot is an intentional
point-in-time restore, and the loop re-snapshots forward post-prune, so any
re-injection is correct restore behaviour, not contamination.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

# Tables carrying an explicit strategy column → prune by ``col NOT IN live``.
# (table, strategy_column)
_STRATEGY_COLUMN_TABLES: tuple[tuple[str, str], ...] = (
    ("cell_matrix_p0", "strategy"),
    ("cell_matrix_parent2", "strategy"),
    ("cell_matrix_parent3", "strategy"),
    ("cell_matrix_shadow_context", "strategy"),
    ("learner_posterior", "strategy"),
    ("strategy_regime_prior", "strategy"),
    ("learner_blocks", "strategy_id"),
)

# ``learner_state`` embeds the strategy in ``key``; the delimiter + token index
# vary by ``learner_id``. Anything not listed here is left untouched (unknown
# key shape → conservative no-op, degrade-never-crash).
#   max_hold     : "<strategy>"  or  "<strategy>:bucket:N"   → split(':')[0]
#   regime_mult  : "<strategy>:<regime>"                     → split(':')[0]
#   session_mult : "<strategy>:<session>"                    → split(':')[0]
#   triple_stats : "<ticker>|<strategy>|<regime>"            → split('|')[1]
_STATE_KEY_PARSERS: dict[str, tuple[str, int]] = {
    "max_hold": (":", 0),
    "regime_mult": (":", 0),
    "session_mult": (":", 0),
    "triple_stats": ("|", 1),
}


class PruneReport(NamedTuple):
    """How many dead-strategy rows were removed, per table + total."""

    by_table: dict[str, int]
    total_deleted: int


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _strategy_from_state_key(learner_id: str, key: str) -> str | None:
    """Extract the strategy token from a ``learner_state`` key, or None.

    Returns None for unknown ``learner_id`` shapes or malformed keys so the
    caller leaves the row untouched (never guess → never delete a live row).
    """
    parser = _STATE_KEY_PARSERS.get(learner_id)
    if parser is None:
        return None
    delimiter, index = parser
    parts = key.split(delimiter)
    if len(parts) <= index:
        return None
    token = parts[index]
    return token or None


def prune_dead_strategies(
    conn: sqlite3.Connection,
    *,
    live_strategy_ids: Iterable[str],
) -> PruneReport:
    """Delete every learner/cell row whose strategy is not in ``live_strategy_ids``.

    Runs under one transaction. Returns a per-table deletion count. The ``fills``
    ledger and ``positions`` are never touched. Raises ``ValueError`` when
    ``live_strategy_ids`` is empty (refuses a full wipe — guards against a
    registry-import failure deleting the whole matrix).
    """
    live = frozenset(live_strategy_ids)
    if not live:
        raise ValueError(
            "refusing to prune against an empty live-strategy set "
            "(would delete the entire learner/cell matrix)"
        )

    by_table: dict[str, int] = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, col in _STRATEGY_COLUMN_TABLES:
            by_table[table] = _prune_strategy_column(conn, table, col, live)
        by_table["learner_state"] = _prune_learner_state(conn, live)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    total = sum(by_table.values())
    return PruneReport(by_table=by_table, total_deleted=total)


def _prune_strategy_column(
    conn: sqlite3.Connection,
    table: str,
    col: str,
    live: frozenset[str],
) -> int:
    """Delete rows of ``table`` whose ``col`` is not in ``live``. 0 if missing."""
    if not _table_exists(conn, table):
        return 0
    placeholders = ",".join("?" for _ in live)
    cur = conn.execute(
        f"DELETE FROM {table} WHERE {col} NOT IN ({placeholders})",  # noqa: S608
        tuple(live),
    )
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def _prune_learner_state(conn: sqlite3.Connection, live: frozenset[str]) -> int:
    """Delete ``learner_state`` rows whose key-embedded strategy is not live.

    Parses each row's strategy from its key (format depends on ``learner_id``);
    rows with an unrecognised key shape are left alone (conservative).
    """
    if not _table_exists(conn, "learner_state"):
        return 0
    doomed: list[tuple[str, str]] = []
    for learner_id, key in conn.execute(
        "SELECT learner_id, key FROM learner_state"
    ).fetchall():
        strategy = _strategy_from_state_key(learner_id, key)
        if strategy is not None and strategy not in live:
            doomed.append((learner_id, key))
    conn.executemany(
        "DELETE FROM learner_state WHERE learner_id = ? AND key = ?", doomed
    )
    return len(doomed)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _live_ids_from_registry() -> set[str]:
    """Current registered strategy ids (the live set). Lazy import."""
    from polaris.strategies import STRATEGY_REGISTRY

    return set(STRATEGY_REGISTRY.keys())


def main(argv: list[str] | None = None) -> int:
    """Prune dead-strategy learner/cell rows from a Polaris SQLite DB.

    Live set = current ``STRATEGY_REGISTRY``. DEMO/PAPER; data-integrity only —
    no trading behaviour change (flow_not_block; fills ledger untouched). Use
    ``--dry-run`` to report counts without deleting.
    """
    parser = argparse.ArgumentParser(
        prog="learner_prune",
        description=(
            "Prune learner/cell rows for KILLed (un-registered) strategies. "
            "DEMO; data-integrity only — preserves live-strategy warming + the "
            "fills audit ledger. Idempotent."
        ),
    )
    parser.add_argument(
        "--db", default="data/polaris_live.sqlite", help="SQLite DB path"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report dead-row counts without deleting",
    )
    args = parser.parse_args(argv)

    live = _live_ids_from_registry()

    from polaris.storage.schema import init_db

    conn = init_db(Path(args.db))
    try:
        if args.dry_run:
            report = _dry_run_counts(conn, live)
            verb = "would delete"
        else:
            report = prune_dead_strategies(conn, live_strategy_ids=live)
            conn.commit()
            verb = "deleted"
    finally:
        conn.close()

    print(f"learner_prune ({verb}) — live strategies: {len(live)}")
    for table, n in report.by_table.items():
        if n:
            print(f"  {table}: {n}")
    print(f"  TOTAL {verb}: {report.total_deleted}")
    return 0


def _dry_run_counts(conn: sqlite3.Connection, live: set[str]) -> PruneReport:
    """Count dead rows per table WITHOUT deleting (read-only)."""
    live_fs = frozenset(live)
    if not live_fs:
        raise ValueError("empty live set")
    by_table: dict[str, int] = {}
    for table, col in _STRATEGY_COLUMN_TABLES:
        if not _table_exists(conn, table):
            by_table[table] = 0
            continue
        placeholders = ",".join("?" for _ in live_fs)
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {col} NOT IN ({placeholders})",  # noqa: S608
            tuple(live_fs),
        ).fetchone()[0]
        by_table[table] = int(n)
    dead_state = 0
    if _table_exists(conn, "learner_state"):
        for learner_id, key in conn.execute(
            "SELECT learner_id, key FROM learner_state"
        ).fetchall():
            strategy = _strategy_from_state_key(learner_id, key)
            if strategy is not None and strategy not in live_fs:
                dead_state += 1
    by_table["learner_state"] = dead_state
    return PruneReport(by_table=by_table, total_deleted=sum(by_table.values()))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
