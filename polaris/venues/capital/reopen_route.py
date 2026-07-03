"""Capital market-closed reject → delay-route (P0 timetable-aware submit gap).

``seconds_to_next_open`` (opening_hours.py) computes the right countdown but
had zero production consumers — a Capital open reject during a closed window
(SG25-style '16/16 reject': every signal on the closed epic re-submits and
re-rejects every tick with no delay-route) just churned. This module stamps
the FIRST such reject with the computed next-open instant (reusing the
reject-anchor upsert-by-key pattern, same as
:func:`polaris.core.isolation.reentry.stamp_reentry_anchor` — no new table
FAMILY invented, one small dedicated table) and gates the next submit
attempt on that exact (venue, symbol, strategy_id, side) key until the
stamped instant has passed.

flow_not_block: this is a DEFERRAL, never a permanent block — the same
signal resumes submitting the instant the window opens (the stamp is simply
no longer in the future); nothing shrinks size/strength, nothing is skipped
forever. DEMO/PAPER only (Capital CFD demo track).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Final

from polaris.venues.capital.constraint_translator import CapitalMarketConstraint
from polaris.venues.capital.opening_hours import seconds_to_next_open

logger = logging.getLogger(__name__)

__all__ = [
    "MARKET_CLOSED_REJECT_CODES",
    "clear_reopen_stamp",
    "is_market_closed_reject",
    "maybe_stamp_delayed_reopen",
    "submit_suppressed_until_reopen",
]

# The "currently closed" reject family (StreamConfig B_capital_cfd.
# external_reject_codes carries the full non-fault set; only these two are a
# TIMING condition where retrying at the next open window helps).
# INSTRUMENT_NOT_TRADEABLE / REJECTED are NOT included — those are not a
# closed-window condition and retrying-at-next-open would not fix them.
MARKET_CLOSED_REJECT_CODES: Final[frozenset[str]] = frozenset(
    {"MARKET_CLOSED", "MARKET_OFFLINE"}
)


def is_market_closed_reject(reject_code: str | None) -> bool:
    """True only for the market-closed/offline reject family."""
    return reject_code in MARKET_CLOSED_REJECT_CODES


def maybe_stamp_delayed_reopen(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    side: str,
    reject_code: str | None,
    constraint: CapitalMarketConstraint | None,
    now_ts: int,
) -> bool:
    """Stamp the delayed-reopen route for a market-closed reject.

    Returns ``True`` iff a stamp was written. ``False`` (no-op) when: the
    reject is not in the market-closed family, the constraint/opening_hours
    is unavailable, the epic is FX (24/5-exempt by type — legacy Friday
    branch owns it), or ``seconds_to_next_open`` returns ``None`` (already
    inside an open window, or no open window found in the lookahead — never
    fabricate a countdown). Idempotent UPSERT keyed on
    (venue, symbol, strategy_id, side) — a repeat reject during the SAME
    closed window just refreshes the same row. Best-effort: a DB error is
    logged, never raised (mirrors ``stamp_reentry_anchor``).
    """
    if not is_market_closed_reject(reject_code):
        return False
    if constraint is None or constraint.opening_hours is None:
        return False
    is_fx = constraint.instrument_type == "CURRENCIES"
    secs = seconds_to_next_open(constraint.opening_hours, now_ts, is_fx=is_fx)
    if secs is None:
        return False
    reopen_at_ts = now_ts + secs
    try:
        conn.execute(
            "INSERT INTO capital_reopen_pending "
            "(venue, symbol, strategy_id, side, reopen_at_ts, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(venue, symbol, strategy_id, side) DO UPDATE SET "
            "reopen_at_ts = excluded.reopen_at_ts",
            (venue, symbol, strategy_id, side.lower(), reopen_at_ts, now_ts),
        )
        conn.commit()
    except sqlite3.Error as exc:  # fail-open — never block on DB trouble.
        logger.warning("[capital/reopen-route] stamp failed: %s", exc)
        return False
    logger.info(
        "[capital/reopen-route] %s:%s sid=%s side=%s market-closed (%s) — "
        "delayed re-route to reopen_at_ts=%d (+%ds)",
        venue, symbol, strategy_id, side, reject_code, reopen_at_ts, secs,
    )
    return True


def submit_suppressed_until_reopen(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    side: str,
    now_ts: int,
) -> bool:
    """True when a submit on this exact key should be SKIPPED this tick.

    Reads the stamped ``reopen_at_ts`` for (venue, symbol, strategy_id,
    side); suppressed while ``now_ts < reopen_at_ts``. No stamp, or the
    stamped instant has arrived/passed → ``False`` (submit proceeds
    normally — flow resumes, never a permanent hold). Any DB error is
    fail-open (``False`` = allow) so this guard can never wedge the entry
    loop.
    """
    try:
        row = conn.execute(
            "SELECT reopen_at_ts FROM capital_reopen_pending "
            "WHERE venue = ? AND symbol = ? AND strategy_id = ? AND side = ?",
            (venue, symbol, strategy_id, side.lower()),
        ).fetchone()
    except sqlite3.Error as exc:  # fail-open — never block on DB trouble.
        logger.warning("[capital/reopen-route] lookup failed: %s", exc)
        return False
    if row is None or row[0] is None:
        return False
    return now_ts < int(row[0])


def clear_reopen_stamp(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    side: str,
) -> None:
    """Remove the stamp once the delayed re-route has resolved (a fresh fill
    or a non-market-closed outcome on this key). Best-effort."""
    try:
        conn.execute(
            "DELETE FROM capital_reopen_pending "
            "WHERE venue = ? AND symbol = ? AND strategy_id = ? AND side = ?",
            (venue, symbol, strategy_id, side.lower()),
        )
        conn.commit()
    except sqlite3.Error as exc:  # fail-open — never block on DB trouble.
        logger.warning("[capital/reopen-route] clear failed: %s", exc)
