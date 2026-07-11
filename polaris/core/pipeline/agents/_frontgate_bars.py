"""Shared known-at-time 1m bars reader for G4 frontgate SHADOW taggers.

DEMO/PAPER, behavior-0 instrumentation only. VWAP/AVWAP timing (item #6) and
TTM Squeeze (item #9) both need the same "1m bars strictly known at G4
decision time" canvas. ``core/`` cannot import ``polaris.scripts`` (layering —
scripts depends on core, never the reverse), so this is a minimal, dedicated
read against ``bars`` instead of reusing ``_production_bars.read_recent_bars``
(which also bounds its window off wall-clock ``now``, not an arbitrary
``decision_ts`` — unsuitable for a strict look-ahead guard).

Look-ahead guard (frontgate-scan integration-blueprint item #6, verifier
note #5): only bars with ``ts <= floor_to_completed_minute(decision_ts) - 60``
are ever returned — the still-forming AND the most-recently-closed 1m bar are
both excluded so a shadow tag can never see a bar that was not yet fully known
at the G4 decision instant.
"""

from __future__ import annotations

import sqlite3
from typing import Final

from polaris.strategies.base import BarView

__all__ = ["FRONTGATE_BAR_FETCH_LIMIT", "bar_cutoff_ts", "fetch_known_bars"]

# ~25h of 1m bars — comfortably covers both anchor windows (session-open
# boundary walks back at most 24h; signal-day AVWAP is bounded by UTC midnight).
FRONTGATE_BAR_FETCH_LIMIT: Final[int] = 1_500


def bar_cutoff_ts(decision_ts: int) -> int:
    """Latest bar ``ts`` a G4 decision at ``decision_ts`` may legally see.

    ``floor_to_completed_minute(decision_ts) - 60``: floors to the most-recent
    completed minute boundary, then excludes one more bar so the
    just-closed-but-maybe-still-settling 1m candle is never used either.
    """
    floored = (int(decision_ts) // 60) * 60
    return floored - 60


def fetch_known_bars(
    conn: sqlite3.Connection | None,
    *,
    venue: str,
    symbol: str,
    decision_ts: int,
    limit: int = FRONTGATE_BAR_FETCH_LIMIT,
) -> list[BarView]:
    """1m bars for ``venue:symbol`` with ``ts <= bar_cutoff_ts(decision_ts)``.

    Ascending order (oldest first) — matches ``session_anchor_index``'s
    expected ordering. Fail-open: ``conn is None`` or any ``sqlite3.Error``
    returns ``[]`` (instrumentation must never crash the live gate).
    """
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT ts, open, high, low, close, volume
            FROM bars
            WHERE instrument_id = ? AND bar_interval = '1m' AND ts <= ?
            ORDER BY ts DESC LIMIT ?
            """,
            (f"{venue}:{symbol}", bar_cutoff_ts(decision_ts), int(limit)),
        ).fetchall()
    except sqlite3.Error:
        return []
    views = [
        BarView(
            ts=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
            close=float(r[4]), volume=float(r[5]),
        )
        for r in rows
    ]
    views.reverse()
    return views
