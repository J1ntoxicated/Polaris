"""Layer 2 — shared best-effort query helper for pipeline payload builders.

Split out so ``payload_builder`` (G3/G4/G6/G7 + active positions) and
``_sizer_payload`` (G5) can share ``_safe_query`` without a circular import.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any


def _safe_query(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] = (),
) -> list[Any]:
    """Best-effort query — returns ``[]`` when the table is missing."""
    try:
        cur = conn.execute(sql, tuple(params))
        return list(cur.fetchall())
    except sqlite3.Error:
        return []
