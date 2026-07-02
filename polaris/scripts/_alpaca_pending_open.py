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

import contextlib
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from polaris.core.data.fill_normalizer import ALPACA_FILLED_STATES

logger = logging.getLogger(__name__)

__all__ = [
    "PendingOpenRef",
    "clear_pending_open",
    "read_pending_open",
    "true_up_alpaca_partial",
    "upsert_pending_open",
]


@dataclass(frozen=True, slots=True)
class PendingOpenRef:
    """A previously-submitted, still-unconfirmed Alpaca open order.

    ``state`` — ``'no_fill'`` (zero confirmed qty; a fill resolves it fresh)
    or ``'partial_trueup'`` (a position ALREADY EXISTS from the snapshot fill;
    ``position_id`` names the row the next tick's true-up folds the delta into).
    """

    venue_order_id: str
    client_order_id: str | None
    notional_usd: float
    last_price: float | None
    state: str = "no_fill"
    position_id: str | None = None


def read_pending_open(
    conn: sqlite3.Connection, *, venue: str, symbol: str, strategy_id: str, side: str,
) -> PendingOpenRef | None:
    """Look up a carried-over unconfirmed open for this exact key, if any."""
    try:
        row = conn.execute(
            "SELECT venue_order_id, client_order_id, notional_usd, last_price, "
            "state, position_id "
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
        state=str(row[4]) if row[4] is not None else "no_fill",
        position_id=str(row[5]) if row[5] is not None else None,
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
    state: str = "no_fill",
    position_id: str | None = None,
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
                 notional_usd, last_price, state, position_id, created_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(venue, symbol, strategy_id, side) DO UPDATE SET
                venue_order_id=excluded.venue_order_id,
                client_order_id=excluded.client_order_id,
                notional_usd=excluded.notional_usd,
                last_price=excluded.last_price,
                state=excluded.state,
                position_id=excluded.position_id,
                updated_ts=excluded.updated_ts
            """,
            (
                venue, symbol, strategy_id, side.lower(), venue_order_id,
                client_order_id, notional_usd, last_price, state, position_id,
                now_ts, now_ts,
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


async def true_up_alpaca_partial(
    conn: sqlite3.Connection, adapter: Any, ref: PendingOpenRef, *,
    venue: str, symbol: str, strategy_id: str, side: str,
) -> None:
    """Fold a ``partial_trueup`` ref's FINAL venue qty into its position.

    Fetches the order; if ``filled_qty`` grew past the snapshot already
    persisted (``positions.qty`` for ``ref.position_id``), fold the delta into
    ``positions.qty`` + the ORIGINAL open ``fills`` row (base_qty/size_usd/
    quote_qty) and re-stamp ``risk_usd`` proportionally to the new qty (entry
    price anchor unchanged — NOT a re-sizing decision). Still
    ``partially_filled`` → refresh the snapshot and keep the ref for the tick
    after. Terminal (``filled`` or dead) → fold the final qty (if any growth)
    then clear the ref. A fetch failure keeps the ref for a later retry
    (best-effort, never fabricates a fill). No entries are blocked by this —
    pure post-hoc ledger honesty (flow_not_block).
    """
    if not ref.position_id:
        clear_pending_open(conn, venue=venue, symbol=symbol,
                            strategy_id=strategy_id, side=side)
        return
    try:
        row = await adapter.fetch_order(order_id=ref.venue_order_id)
    except Exception as exc:  # noqa: BLE001 — venue read is best-effort
        logger.warning(
            "[alpaca/trueup] %s order %s fetch failed %r — keep pending",
            symbol, ref.venue_order_id, exc,
        )
        return
    state = str((row or {}).get("status") or "").lower()
    if not row or state not in ALPACA_FILLED_STATES:
        # No confirmed fill data (terminal-dead or a malformed read) — the
        # snapshot already persisted stands as final; nothing to fold.
        clear_pending_open(conn, venue=venue, symbol=symbol,
                            strategy_id=strategy_id, side=side)
        return
    try:
        final_qty = float(row.get("filled_qty") or 0.0)
        final_px = float(row.get("filled_avg_price") or 0.0)
    except (TypeError, ValueError):
        clear_pending_open(conn, venue=venue, symbol=symbol,
                            strategy_id=strategy_id, side=side)
        return
    if final_qty <= 0.0 or final_px <= 0.0:
        clear_pending_open(conn, venue=venue, symbol=symbol,
                            strategy_id=strategy_id, side=side)
        return
    pos_row = conn.execute(
        "SELECT qty, risk_usd FROM positions WHERE position_id = ?",
        (ref.position_id,),
    ).fetchone()
    if pos_row is None:
        # Position closed/reconciled since the snapshot — nothing left to fold.
        clear_pending_open(conn, venue=venue, symbol=symbol,
                            strategy_id=strategy_id, side=side)
        return
    old_qty, old_risk_usd = float(pos_row[0] or 0.0), pos_row[1]
    delta = final_qty - old_qty
    if delta > 0.0 and old_qty > 0.0:
        new_risk_usd = (
            float(old_risk_usd) * (final_qty / old_qty)
            if old_risk_usd is not None else None
        )
        new_quote_qty = final_qty * final_px
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE positions SET qty = ?, risk_usd = COALESCE(?, risk_usd) "
                "WHERE position_id = ?",
                (final_qty, new_risk_usd, ref.position_id),
            )
            conn.execute(
                "UPDATE fills SET base_qty = ?, size_usd = ?, quote_qty = ? "
                "WHERE order_id = ? AND is_close = 0",
                (final_qty, new_quote_qty, new_quote_qty, ref.venue_order_id),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            logger.error(
                "[alpaca/trueup] %s position=%s fold UPDATE failed "
                "(state preserved): %r", symbol, ref.position_id, exc,
            )
            return
        logger.warning(
            "[alpaca/trueup] %s position=%s qty %.9f -> %.9f "
            "(delta=%.9f folded, risk_usd rescaled)",
            symbol, ref.position_id, old_qty, final_qty, delta,
        )
    if state == "partially_filled":
        # Still growing — refresh the snapshot notional/price and keep polling.
        upsert_pending_open(
            conn, venue=venue, symbol=symbol, strategy_id=strategy_id, side=side,
            venue_order_id=ref.venue_order_id, client_order_id=ref.client_order_id,
            notional_usd=final_qty * final_px, last_price=final_px,
            now_ts=int(time.time()),
            state="partial_trueup", position_id=ref.position_id,
        )
        return
    clear_pending_open(conn, venue=venue, symbol=symbol,
                        strategy_id=strategy_id, side=side)
