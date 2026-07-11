"""Filing-proximity SHADOW (frontgate-scan item #1, G4, behavior-0).

DEMO/PAPER, virtual capital. One row per (symbol, collection cycle) recording
how far the ``edgar_events`` collector's own "now" sits from that symbol's
latest qualifying (8-K/10-Q/10-K) filing's ``acceptanceDateTime`` — a pure
TAG-ONLY context stamp, mirroring ``news_timing_shadow.py``'s pattern (called
from inside the collector's own fetch cadence, not from a live gate).

TAG-ONLY: never read by any live gate/sizing/exit path (frontgate-scan
behavior-0 invariant). Wiring a G4 consumer is explicit follow-up work.
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3

logger = logging.getLogger(__name__)

__all__ = ["log_filing_proximity_shadow"]


def log_filing_proximity_shadow(
    conn: sqlite3.Connection | None,
    *,
    symbol: str,
    cycle_ts: int,
    form_type: str,
    accession_number: str,
    acceptance_ts: int | None,
    now: _dt.datetime,
) -> int:
    """Insert one shadow row; no-op (0) on a ``None`` conn or missing symbol.

    Fail-open on any ``sqlite3.Error`` — instrumentation must never break the
    collector's own return value. Idempotent within a cycle via the table's
    ``PRIMARY KEY (symbol, cycle_ts)``.
    """
    if conn is None or not symbol:
        return 0
    days_since = (
        round(max(0.0, now.timestamp() - acceptance_ts) / 86400.0, 3)
        if acceptance_ts is not None
        else None
    )
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO filing_proximity_shadow "
            "(symbol, cycle_ts, form_type, days_since_filing, accession_number, "
            " created_ts) VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, cycle_ts, form_type, days_since, accession_number, cycle_ts),
        )
    except sqlite3.Error as exc:
        logger.warning("[filing_proximity_shadow] log dropped: %r", exc)
        return 0
    return cur.rowcount if cur.rowcount > 0 else 0
