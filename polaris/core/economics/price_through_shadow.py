"""Price-through maker-fill shadow (maker_fill_sim R1 2026-07-12 debate) — SHADOW only.

DEMO/PAPER — virtual account (``real_roundtrip`` is always ``False`` now,
[[feedback_virtual_account_first_then_real_wire]]), so ``maker_fill_shadow``
(maker-only, requires a REAL post-only fill) stays honestly 0-row: no real
maker fill can ever occur. This module records ONE row per REAL entry fill —
the current all-taker execution — carrying the touch a passive maker limit
would have rested at (best-effort read of ``quote_ticks`` by the caller).

Unlike ``maker_fill_shadow``, resolution (did the market subsequently trade
through the touch — the maker WOULD have filled — or not, in which case the
forward move is the missed-opportunity cost) is NEVER decided at write time
(the forward bars do not exist yet); it is computed OFFLINE against the
already-ingested ``bars`` (mirrors ``polaris/core/economics/shadow_order.py``'s
``weekend_shadow_orders`` resolution basis, and
``tools/visualizer/weekend_data.py``'s ``resolve_would_be_r``) — see
``resolve_price_through`` and ``tools/visualizer/price_through_channel.py``.

Instrumentation ONLY — never influences the live entry/exit decision, sizing,
or the taker fill itself. A None conn, a non-positive touch/fill, or a missing
table is a no-op (degrade-never-crash): the fill already happened and must
never be undone or fabricated by a shadow-log failure.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import TYPE_CHECKING, NamedTuple

from polaris.core.economics.fees import real_fee_bps
from polaris.storage.db_writer import dbwriter_enabled

if TYPE_CHECKING:
    from polaris.storage.db_writer import DBWriter

logger = logging.getLogger(__name__)

__all__ = [
    "PriceThroughOutcome",
    "log_price_through_entry",
    "resolve_price_through",
]

# Shared by both the direct-conn and db_writer-submitted paths (mirrors
# ``tsmom_literature_shadow.py``'s pattern) so the two branches are
# structurally guaranteed to issue the identical statement.
_PRICE_THROUGH_SHADOW_SQL = (
    "INSERT INTO price_through_shadow "
    "(event_id, run_id, strategy_id, venue, symbol, side, "
    " fill_px, touch_px, current_fee_bps, created_ts) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def log_price_through_entry(
    conn: sqlite3.Connection | None,
    *,
    run_id: str,
    strategy_id: str,
    venue: str,
    symbol: str,
    side: str,
    fill_px: float,
    touch_px: float,
    now_ts: int,
    db_writer: DBWriter | None = None,
) -> None:
    """Append ONE ``price_through_shadow`` row for a REAL entry fill.

    No-op when ``conn`` is None or either price is non-positive (a missing
    touch is a skip, never a fabricated row). ``db_writer`` (db-writer-reader-
    split): when wired and enabled, the INSERT is submitted as a fire-and-
    forget job to the shared single-RW-conn writer instead of running on the
    caller's own ``conn`` — safe to defer, this row has no same-tick reader
    (resolution is offline, see module docstring). ``None`` falls back to a
    direct ``conn.execute``. A sqlite error is logged and swallowed
    (degrade-never-crash).
    """
    if conn is None or fill_px <= 0.0 or touch_px <= 0.0:
        return
    args = (
        uuid.uuid4().hex,
        run_id,
        strategy_id,
        venue,
        symbol,
        side,
        float(fill_px),
        float(touch_px),
        real_fee_bps(venue, is_maker=False),
        int(now_ts),
    )
    try:
        if db_writer is not None and dbwriter_enabled():

            def _job(
                c: sqlite3.Connection,
                sql: str = _PRICE_THROUGH_SHADOW_SQL,
                a: tuple[object, ...] = args,
            ) -> None:
                c.execute(sql, a)

            db_writer.submit(_job, label="price_through_shadow")
        else:
            conn.execute(_PRICE_THROUGH_SHADOW_SQL, args)
    except sqlite3.Error as exc:
        logger.warning(
            "[price_through_shadow] entry dropped %s:%s: %r", venue, symbol, exc,
        )


class PriceThroughOutcome(NamedTuple):
    """Resolved offline outcome of one price-through shadow row.

    ``traded_through`` — the forward bars swept the resting touch (the maker
    WOULD have filled there). ``price_improve_bps`` — how much cheaper the
    maker fill would have landed vs the actual taker fill (positive = better);
    0.0 when unfilled. ``missed_move_bps`` — when unfilled, how far price moved
    AWAY from the resting touch by the last forward bar (the opportunity cost
    of resting instead of taking); 0.0 when filled.
    """

    traded_through: bool
    price_improve_bps: float
    missed_move_bps: float


def resolve_price_through(
    *,
    side: str,
    touch_px: float,
    fill_px: float,
    forward_highs: list[float],
    forward_lows: list[float],
    forward_closes: list[float],
) -> PriceThroughOutcome:
    """Resolve one shadow row against the bars AFTER its entry (OFFLINE, pure).

    BUY: ``touch_px`` is the bid a passive order rests at (below the taker
    ``fill_px``) — traded-through when a forward bar's low reaches it.
    SELL: ``touch_px`` is the ask (above ``fill_px``) — traded-through when a
    forward bar's high reaches it. Walking the forward bars in order, the
    FIRST bar that reaches the touch resolves ``traded_through=True``. If none
    do, the last forward close's distance from the touch is the missed-
    opportunity move. Degenerate inputs (no bars, non-positive prices) → a
    flat/unresolved outcome. Pure, no I/O — mirrors
    ``tools/visualizer/weekend_data.resolve_would_be_r``'s forward-bar-walk
    shape for the SAME "resolve against future bars" problem.
    """
    if touch_px <= 0.0 or fill_px <= 0.0 or not forward_closes:
        return PriceThroughOutcome(False, 0.0, 0.0)
    is_buy = side.strip().lower() in ("buy", "long")
    highs = forward_highs if forward_highs else forward_closes
    lows = forward_lows if forward_lows else forward_closes
    n = len(forward_closes)
    for i in range(n):
        hi = highs[i] if i < len(highs) else forward_closes[i]
        lo = lows[i] if i < len(lows) else forward_closes[i]
        if is_buy:
            if lo <= touch_px:
                improve = (fill_px - touch_px) / fill_px * 10_000.0
                return PriceThroughOutcome(True, improve, 0.0)
        else:
            if hi >= touch_px:
                improve = (touch_px - fill_px) / fill_px * 10_000.0
                return PriceThroughOutcome(True, improve, 0.0)
    last = forward_closes[-1]
    if is_buy:
        missed = (last - touch_px) / touch_px * 10_000.0
    else:
        missed = (touch_px - last) / touch_px * 10_000.0
    return PriceThroughOutcome(False, 0.0, missed)
