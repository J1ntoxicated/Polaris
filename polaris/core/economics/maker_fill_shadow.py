"""Real-fee maker-fill shadow measurement (#77 component C) — SHADOW only.

OKX **demo** bills a FLAT 70 bps (taker == maker), so the maker edge is
INVISIBLE in demo P&L — a maker fill and a taker fill cost the same on demo.
The single verified maker BUILD (``weekend_thin_book_flush_maker``) is +73 bps
on the REAL fee schedule, so its edge can ONLY be proven by a real-fee shadow,
never by the demo balance. This module records, per maker (post-only) fill:

  * **entry-BASIS** — how much better the fill landed than the touch the bid was
    posted at (a passive post-only fills AT or better than the touch; a positive
    basis is the microstructure premium the weekend thesis harvests);
  * the **real-fee NET** via ``real_fee_bps(is_maker=True)`` (OKX spot maker
    8 bps/leg → 16 bps round trip) — the ONLY place the maker edge is measurable
    before go-live;
  * a **clean_fill / pick_off** outcome label so the asymmetry (clean revert vs
    adverse-selection) is countable, plus the repost count.

Instrumentation ONLY — it never influences the live entry/exit decision. A None
conn or a missing table is a no-op (degrade-never-crash): the fill already
happened and must never be undone by a shadow-log failure.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING

from polaris.core.economics.fees import real_fee_bps
from polaris.storage.db_writer import dbwriter_enabled

if TYPE_CHECKING:
    from polaris.storage.db_writer import DBWriter

logger = logging.getLogger(__name__)

__all__ = [
    "compute_entry_basis_bps",
    "log_maker_fill",
    "maker_net_bps",
]

_MAKER_FILL_SHADOW_SQL = (
    "INSERT INTO maker_fill_shadow "
    "(event_id, run_id, strategy_id, venue, symbol, side, "
    " touch_px, fill_px, entry_basis_bps, real_maker_net_bps, "
    " outcome, reposts, created_ts) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def compute_entry_basis_bps(*, side: str, touch_px: float, fill_px: float) -> float:
    """Entry basis in bps: how much BETTER the fill landed than the touch.

    For a BUY a fill BELOW the touch is favourable (positive bps); for a SELL a
    fill ABOVE the touch is favourable. Exactly at the touch → 0. A degenerate /
    non-positive touch → 0 (no manufactured basis). Pure, no I/O.
    """
    if touch_px <= 0.0 or fill_px <= 0.0:
        return 0.0
    if side == "buy":
        # better = filled cheaper than the posted bid.
        return (touch_px - fill_px) / touch_px * 10_000.0
    # sell: better = filled richer than the posted ask.
    return (fill_px - touch_px) / touch_px * 10_000.0


def maker_net_bps(
    *, venue: str, entry_basis_bps: float, gross_move_bps: float
) -> float:
    """Real-fee NET edge in bps for a maker round trip.

    ``net = gross_move_bps + entry_basis_bps - real_maker_round_trip_bps``, where
    the round trip is ``2 × real_fee_bps(venue, is_maker=True)`` (OKX 8 bps/leg →
    16 bps). The demo 70 bps is NEVER used — that is the whole point of the
    shadow. ``gross_move_bps`` is the realised price move at exit (0 at fill
    time when only the entry basis is known). Pure, no I/O.
    """
    round_trip = 2.0 * real_fee_bps(venue, is_maker=True)
    return gross_move_bps + entry_basis_bps - round_trip


def log_maker_fill(
    conn: sqlite3.Connection | None,
    *,
    run_id: str,
    strategy_id: str,
    venue: str,
    symbol: str,
    side: str,
    touch_px: float,
    fill_px: float,
    outcome: str,
    reposts: int,
    gross_move_bps: float = 0.0,
    db_writer: DBWriter | None = None,
) -> None:
    """Append one ``maker_fill_shadow`` row (entry basis + real-maker net).

    No-op when ``conn`` is None. A sqlite error (e.g. a legacy DB without the
    table) is logged and swallowed — the fill already executed and must never be
    crashed by a shadow-log failure (degrade-never-crash).

    ``db_writer`` (storage-split, mirrors ``price_through_shadow.
    log_price_through_entry``): when wired and enabled, the INSERT is
    submitted as a fire-and-forget job to the marketdata-domain writer INSIDE
    the caller's still-open entry txn (safe to defer — this row has no
    same-tick reader). ``None``/disabled falls back to a direct
    ``conn.execute`` (byte-identical for every existing caller/test).
    """
    if conn is None:
        return
    basis = compute_entry_basis_bps(side=side, touch_px=touch_px, fill_px=fill_px)
    net = maker_net_bps(
        venue=venue, entry_basis_bps=basis, gross_move_bps=gross_move_bps
    )
    args = (
        uuid.uuid4().hex,
        run_id,
        strategy_id,
        venue,
        symbol,
        side,
        float(touch_px),
        float(fill_px),
        float(basis),
        float(net),
        outcome,
        int(reposts),
        int(time.time()),
    )
    try:
        if db_writer is not None and dbwriter_enabled():

            def _job(
                c: sqlite3.Connection,
                sql: str = _MAKER_FILL_SHADOW_SQL,
                a: tuple[object, ...] = args,
            ) -> None:
                c.execute(sql, a)

            db_writer.submit(_job, label="maker_fill_shadow")
        else:
            conn.execute(_MAKER_FILL_SHADOW_SQL, args)
    except sqlite3.Error as exc:
        logger.error("[maker-fill-shadow] durable record failed: %r", exc)
