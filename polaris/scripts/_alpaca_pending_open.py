"""Alpaca OPEN-side pending-confirm carryover (open-confirm fix, Track C).

ROOT CAUSE (live 2026-07-02 13:31-33 UTC open): a market BUY POSTs
``ok=True code=pending_new`` (open-auction delay) and the confirm-poll budget in
``real_alpaca_open_fill`` expires before a fill lands — the order is handed to
``record_venue_orphan`` (a passive ``risk_events`` audit row for the periodic
reconciler) and the reservation releases. Seconds later the order fills at the
venue, but nothing re-checks it until the NEXT boot's adopt-import sweep — so
(a) a later signal on the same (venue, symbol, strategy_id, side) can submit a
DUPLICATE buy, and (b) the position is unmanaged (no risk_usd/entry_atr anchor,
no exit engine) for the whole gap.

FIX (flow_not_block — ENABLE tracking, never throttle): persist the accepted
venue order ref in ``pending_opens`` (one row per (venue, symbol, strategy_id,
side)) instead of only the passive orphan record. The NEXT tick's
``reserve_and_submit`` reads it FIRST (confirm-first, mirrors the close leg's
``pending_close_ref``) — a fill completes the normal L7/open persist, a still-
live order skips this tick (no duplicate submit), and a terminal/dead order
clears the ref so a fresh signal can submit again. DB-backed (unlike the close
leg's in-memory-only ref): an open pre-fill has no ``positions`` row yet to
anchor a venue available/over-count clamp on restart, so the ref itself must
survive the gap between ticks.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = [
    "PendingOpenRef",
    "clear_pending_open",
    "read_pending_open",
    "upsert_pending_open",
]


@dataclass(frozen=True, slots=True)
class PendingOpenRef:
    """A previously-submitted, still-unconfirmed Alpaca open order."""

    venue_order_id: str
    client_order_id: str | None
    notional_usd: float
    last_price: float | None


def read_pending_open(
    conn: sqlite3.Connection, *, venue: str, symbol: str, strategy_id: str, side: str,
) -> PendingOpenRef | None:
    """Look up a carried-over unconfirmed open for this exact key, if any."""
    try:
        row = conn.execute(
            "SELECT venue_order_id, client_order_id, notional_usd, last_price "
            "FROM pending_opens WHERE venue=? AND symbol=? AND strategy_id=? AND side=?",
            (venue, symbol, strategy_id, side.lower()),
        ).fetchone()
    except sqlite3.Error as exc:
        logger.error("[alpaca/open] pending_opens read failed: %r", exc)
        return None
    if row is None:
        return None
    return PendingOpenRef(
        venue_order_id=str(row[0]),
        client_order_id=str(row[1]) if row[1] is not None else None,
        notional_usd=float(row[2]),
        last_price=float(row[3]) if row[3] is not None else None,
    )


def upsert_pending_open(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    side: str,
    venue_order_id: str,
    client_order_id: str | None,
    notional_usd: float,
    last_price: float | None,
    now_ts: int,
) -> None:
    """Persist (or refresh) the still-unconfirmed order ref for this key.

    Best-effort: a write failure is logged, never raised (mirrors
    ``record_venue_orphan`` — the venue order already exists regardless of
    whether this bookkeeping row lands).
    """
    try:
        conn.execute(
            """
            INSERT INTO pending_opens
                (venue, symbol, strategy_id, side, venue_order_id, client_order_id,
                 notional_usd, last_price, created_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(venue, symbol, strategy_id, side) DO UPDATE SET
                venue_order_id=excluded.venue_order_id,
                client_order_id=excluded.client_order_id,
                notional_usd=excluded.notional_usd,
                last_price=excluded.last_price,
                updated_ts=excluded.updated_ts
            """,
            (
                venue, symbol, strategy_id, side.lower(), venue_order_id,
                client_order_id, notional_usd, last_price, now_ts, now_ts,
            ),
        )
    except sqlite3.Error as exc:
        logger.error("[alpaca/open] pending_opens upsert failed: %r", exc)


def clear_pending_open(
    conn: sqlite3.Connection, *, venue: str, symbol: str, strategy_id: str, side: str,
) -> None:
    """Remove the carried-over ref (fill confirmed or order terminally dead)."""
    try:
        conn.execute(
            "DELETE FROM pending_opens "
            "WHERE venue=? AND symbol=? AND strategy_id=? AND side=?",
            (venue, symbol, strategy_id, side.lower()),
        )
    except sqlite3.Error as exc:
        logger.error("[alpaca/open] pending_opens clear failed: %r", exc)
