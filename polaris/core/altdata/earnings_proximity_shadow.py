"""Earnings-proximity SHADOW (frontgate-scan item #3, G3, behavior-0).

DEMO/PAPER, virtual capital. One row per (symbol, collection cycle) recording
the signed day-distance to the nearest Finnhub earnings-calendar print (past
or future) + its SUE-proxy surprise (when a recent actual is already known) —
a pure TAG-ONLY context stamp, mirroring ``news_timing_shadow.py``'s pattern
(called from inside the collector's own fetch cadence, not from a live gate).

``surprise_pct`` is a plain (actual-estimate)/|estimate| proxy, NOT a real SUE
(no consensus-revision/std-dev — see
vault/50_research/frontgate-scan/scan-event-models.md R1).

TAG-ONLY: never read by any live gate/sizing/exit path (frontgate-scan
behavior-0 invariant). Wiring a G3 consumer is explicit follow-up work.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

__all__ = ["log_earnings_proximity_shadow"]


def log_earnings_proximity_shadow(
    conn: sqlite3.Connection | None,
    *,
    symbol: str,
    cycle_ts: int,
    days_to_earnings: float | None,
    hour: str,
    surprise_pct: float | None,
) -> int:
    """Insert one shadow row; no-op (0) on a ``None`` conn or missing symbol.

    Fail-open on any ``sqlite3.Error`` — instrumentation must never break the
    collector's own return value. Idempotent within a cycle via the table's
    ``PRIMARY KEY (symbol, cycle_ts)``.
    """
    if conn is None or not symbol:
        return 0
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO earnings_proximity_shadow "
            "(symbol, cycle_ts, days_to_earnings, hour, surprise_pct, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, cycle_ts, days_to_earnings, hour, surprise_pct, cycle_ts),
        )
    except sqlite3.Error as exc:
        logger.warning("[earnings_proximity_shadow] log dropped: %r", exc)
        return 0
    return cur.rowcount if cur.rowcount > 0 else 0
