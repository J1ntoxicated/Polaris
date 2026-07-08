"""Chunk-submit helper for ``refresh_ticker_ground``'s db-writer-reader-split
opt-in path (design SSOT:
``vault/50_research/db-writer-reader-split-design_2026-07-08.md`` §2 W1).

Split out of ``_static_ground.py`` (already >500 LOC) to keep this addition
isolated + independently testable rather than growing that file further.

The legacy path (``db_writer=None``) wraps the WHOLE active-universe walk
(~1882 rows) in ONE explicit ``BEGIN..COMMIT`` on the loop-owned conn — the
dominant write-lock contender the split design targets (a single txn can hold
the WAL write lock for hundreds of ms, starving the 1Hz quote/tech flushes
past their ``busy_timeout``). This module replaces that with many small
chunk jobs submitted to the shared ``DBWriter`` — each chunk is one
``SAVEPOINT`` inside the writer's own batch transaction, so the walk
interleaves with the writer's other traffic instead of monopolizing the lock.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from typing import Any

from polaris.storage.db_writer import DBWriter

__all__ = ["GroundRow", "ground_chunk_rows", "submit_ground_chunks"]

# A pre-computed ``ticker_ground`` row: (instrument, ts, has_sentiment,
# has_event, evidence). Plain data — fuse_evidence has already run (pure
# in-mem) by the time this module ever sees a row.
GroundRow = tuple[Any, int, bool, bool, dict[str, Any]]


def ground_chunk_rows() -> int:
    """``POLARIS_GROUND_CHUNK_ROWS`` env knob (default 200), never hardcoded
    in the submit logic below."""
    raw = os.environ.get("POLARIS_GROUND_CHUNK_ROWS")
    if raw is None or not raw.strip():
        return 200
    try:
        value = int(raw)
    except ValueError:
        return 200
    return value if value > 0 else 200


def submit_ground_chunks(
    db_writer: DBWriter,
    rows: list[GroundRow],
    persist_one: Callable[[sqlite3.Connection, GroundRow], None],
) -> None:
    """Fire-and-forget submit ``rows`` to ``db_writer`` in bounded chunks.

    ``persist_one(conn, row)`` performs the single-row upsert (the caller's
    ``_persist_ticker_ground``) — kept as an injected callable so this module
    has no dependency on ``_static_ground``'s DDL-shaped helpers.
    """
    chunk_size = ground_chunk_rows()
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start : start + chunk_size]

        def _job(
            conn: sqlite3.Connection, chunk: list[GroundRow] = chunk
        ) -> None:
            for row in chunk:
                persist_one(conn, row)

        db_writer.submit(_job, label="ticker_ground_chunk")
