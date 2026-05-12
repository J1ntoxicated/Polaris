"""Layer 7 — strategy_id-scoped namespace helpers over unified tables.

Spec source: vault/30_components/layer-7-strategy-isolation.md (Q4).

P0 design: **single unified tables + explicit ``strategy_id`` column** (separate
tables/databases rejected). The helpers below are thin scoping wrappers so
callers cannot accidentally read/write across strategy boundaries.

The contract every helper enforces:
  - reads: WHERE strategy_id = ?
  - writes: must include strategy_id in the row
  - cross-strategy reads must be explicit (use the underlying conn directly)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "ISOLATION_TABLES",
    "StrategyNamespace",
    "list_open_intents",
    "list_open_positions",
    "list_recent_faults",
]

# Tables backed by ``strategy_id`` column (Q4 unified-table contract).
ISOLATION_TABLES: Final[tuple[str, ...]] = (
    "strategy_halts",
    "strategy_fault_events",
    "allocator_reservations",
    "order_intents",
)


@dataclass(frozen=True, slots=True)
class StrategyNamespace:
    """Scoping wrapper that pins every helper call to one ``strategy_id``."""

    conn: sqlite3.Connection
    strategy_id: str

    def list_open_intents(self) -> list[dict[str, Any]]:
        return list_open_intents(self.conn, self.strategy_id)

    def list_recent_faults(self, *, since_ts: int) -> list[dict[str, Any]]:
        return list_recent_faults(self.conn, self.strategy_id, since_ts=since_ts)

    def list_pending_reservations(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT reservation_id, venue, symbol, order_key, status, expires_ts
            FROM allocator_reservations
            WHERE strategy_id = ? AND status IN ('pending', 'confirmed')
            ORDER BY created_ts ASC
            """,
            (self.strategy_id,),
        ).fetchall()
        return [
            {
                "reservation_id": r[0],
                "venue": r[1],
                "symbol": r[2],
                "order_key": r[3],
                "status": r[4],
                "expires_ts": r[5],
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def list_open_intents(conn: sqlite3.Connection, strategy_id: str) -> list[dict[str, Any]]:
    """Open intents (status ∈ created/submitted/acked) for one strategy only."""
    rows = conn.execute(
        """
        SELECT order_key, venue, symbol, side, status, created_ts
        FROM order_intents
        WHERE strategy_id = ? AND status IN ('created', 'submitted', 'acked')
        ORDER BY created_ts ASC
        """,
        (strategy_id,),
    ).fetchall()
    return [
        {
            "order_key": r[0],
            "venue": r[1],
            "symbol": r[2],
            "side": r[3],
            "status": r[4],
            "created_ts": r[5],
        }
        for r in rows
    ]


def list_recent_faults(
    conn: sqlite3.Connection,
    strategy_id: str,
    *,
    since_ts: int,
) -> list[dict[str, Any]]:
    """Fault events for one strategy since ``since_ts``."""
    rows = conn.execute(
        """
        SELECT event_id, fault_type, event_ts, detail_json
        FROM strategy_fault_events
        WHERE strategy_id = ? AND event_ts >= ?
        ORDER BY event_ts ASC
        """,
        (strategy_id, since_ts),
    ).fetchall()
    return [
        {"event_id": r[0], "fault_type": r[1], "event_ts": r[2], "detail_json": r[3]}
        for r in rows
    ]


def list_open_positions(
    conn: sqlite3.Connection,
    strategy_id: str,
    *,
    statuses: Iterable[str] = ("open", "opening"),
) -> list[dict[str, Any]]:
    """Open positions for ``strategy_id`` (table is provisioned by ``init_db``)."""
    statuses_t = tuple(statuses)
    placeholders = ",".join("?" for _ in statuses_t)
    rows = conn.execute(
        f"""
        SELECT position_id, venue, symbol, side, qty, status
        FROM positions
        WHERE strategy_id = ? AND status IN ({placeholders})
        """,
        (strategy_id, *statuses_t),
    ).fetchall()
    return [
        {
            "position_id": r[0],
            "venue": r[1],
            "symbol": r[2],
            "side": r[3],
            "qty": r[4],
            "status": r[5],
        }
        for r in rows
    ]
