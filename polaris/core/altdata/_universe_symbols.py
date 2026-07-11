"""Shared RO helper — active Alpaca-universe symbols (frontgate-scan feeds).

DEMO/PAPER. Both ``edgar_events`` and ``finnhub_earnings`` need "our active
Alpaca equity universe" to scope their per-symbol queries. Pure read against
the SAME writer connection threaded through ``_altdata_producer`` (no separate
DB open) — degrades to an empty tuple on any sqlite error or a ``None`` conn
(graceful, mirrors every other collector's keyless-skip shape).
"""

from __future__ import annotations

import sqlite3

_ACTIVE_ALPACA_SQL = "SELECT symbol FROM universe WHERE venue = 'alpaca' AND is_active = 1"


def active_alpaca_symbols(conn: sqlite3.Connection | None) -> tuple[str, ...]:
    """Active (``is_active=1``) Alpaca-venue symbols, upper-cased + de-duped."""
    if conn is None:
        return ()
    try:
        rows = conn.execute(_ACTIVE_ALPACA_SQL).fetchall()
    except sqlite3.Error:
        return ()
    return tuple(sorted({str(r[0]).strip().upper() for r in rows if r and r[0]}))
