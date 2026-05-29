"""Runtime non-tradeable symbol blocklist (Task 3 / D2).

A ``(venue, symbol)`` that the venue permanently refuses on compliance grounds
(OKX US-region ``51155``: e.g. GAS, TRUMP) is recorded here so the focus
producer + the order guard skip it — no reservation, no strategy fault. It is
purely a flow-preserving exclusion: it removes a guaranteed-reject symbol so the
rest of the universe keeps trading (flow_not_block), never dampening sizing or
participation. Only permanent compliance rejects are recorded; transient codes
(insufficient balance, no-fill) are never blocklisted.

SQLite-backed (survives restart). The schema DDL lives in
``polaris.storage.schema_ddl_ext`` (``DDL_VENUE_BLOCKLIST``).
"""

from __future__ import annotations

import sqlite3

__all__ = ["add_blocklist", "is_blocklisted", "load_blocklist"]


def add_blocklist(
    conn: sqlite3.Connection,
    venue: str,
    symbol: str,
    *,
    reason: str,
    code: str,
    now_ts: int,
) -> None:
    """UPSERT a ``(venue, symbol)`` into the blocklist.

    First insert sets ``first_ts``/``last_ts`` to ``now_ts`` and ``hit_count=1``;
    a re-hit keeps ``first_ts``, bumps ``last_ts`` to ``now_ts`` and increments
    ``hit_count`` (so we can observe how often a blocklisted symbol re-triggers).
    """
    conn.execute(
        """
        INSERT INTO venue_blocklist
            (venue, symbol, reason, code, first_ts, last_ts, hit_count)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(venue, symbol) DO UPDATE SET
            last_ts = excluded.last_ts,
            hit_count = venue_blocklist.hit_count + 1,
            reason = excluded.reason,
            code = excluded.code
        """,
        (venue, symbol, reason, code, now_ts, now_ts),
    )


def is_blocklisted(conn: sqlite3.Connection, venue: str, symbol: str) -> bool:
    """True when ``(venue, symbol)`` is on the runtime blocklist."""
    row = conn.execute(
        "SELECT 1 FROM venue_blocklist WHERE venue = ? AND symbol = ? LIMIT 1",
        (venue, symbol),
    ).fetchone()
    return row is not None


def load_blocklist(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """Return every blocklisted ``(venue, symbol)`` as a set (for boot load)."""
    rows = conn.execute("SELECT venue, symbol FROM venue_blocklist").fetchall()
    return {(str(r[0]), str(r[1])) for r in rows}
