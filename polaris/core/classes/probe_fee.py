"""pts-classes (group WIRE) — probe_fee_24h accrual.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
Aggressive bias preserved — this is bookkeeping only (accrual of the round-
trip fee cost a fired PROVE probe is expected to incur), never a block/
throttle. Enforcing the 6/4/2xF_track_cap 24h cap + the 3/2/1 concurrent-probe
cap (prove_then_scale_classes_2026-07-03.md 유효반박 #2) is the daily
07:30 reranker's job (out of this group's scope, per the build plan's group F)
— this module only keeps ``strategy_class.probe_fee_24h`` accurate so that
consumer has real data to read. No sizing/9-stack/-1.0R rail touch.

``probe_fee_24h`` is a single running-total column (group A's DDL), not an
event ledger — the 24h ROLLING aspect (decay/reset) is the reranker's
07:30-cadence concern (its consumption "freezes" the value once daily); this
module only performs the ADDITIVE accrual at probe-fire time.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3

from polaris.core.economics.fees import real_fee_bps

logger = logging.getLogger(__name__)

__all__ = ["accrue_probe_fee"]


def _round_trip_fee_usd(venue: str, notional_usd: float) -> float:
    """Two REAL taker legs (entry+exit) on ``notional_usd`` — same schedule
    source as ``probe_notional.round_trip_fee_rate`` (re-derived here, not
    imported, to keep this module's import graph independent of group D's
    ``probe_notional.py``)."""
    return notional_usd * (2.0 * real_fee_bps(venue) / 10_000.0)


def accrue_probe_fee(
    conn: sqlite3.Connection, *, venue: str, strategy_id: str, notional_usd: float
) -> None:
    """Add this fired probe's expected round-trip fee onto
    ``strategy_class.probe_fee_24h``. Fail-open — an accrual failure must
    never unwind the sizing decision that already fired (flow_not_block); a
    missing ``strategy_class`` row (not yet bootstrapped) is a silent no-op
    (nothing to accrue onto).
    """
    if notional_usd <= 0.0:
        return
    fee_usd = _round_trip_fee_usd(venue, notional_usd)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE strategy_class SET probe_fee_24h = probe_fee_24h + ? "
            "WHERE venue = ? AND strategy_id = ?",
            (fee_usd, venue, strategy_id),
        )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        logger.error(
            "[pts-classes] probe_fee_24h accrual failed for %s/%s: %r",
            venue, strategy_id, exc,
        )
