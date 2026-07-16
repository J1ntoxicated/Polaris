"""universe / quote_ticks single-row read for ``LiquidityProbe`` (P3 promotion).

DEMO/PAPER. Mirrors ``tighten_intent.latest_probe_tighten``'s FAIL-OPEN
single-row-PK-lookup shape — the SAME precedent already accepted in the exact
G6 observe call site this feeds. Both reads are PRIMARY-KEY point lookups
(``universe`` PK is ``(venue, symbol)``; ``quote_ticks`` PK is
``instrument_id``), not scans, so this adds no new query shape to the hot
path — only two more indexed reads of the SAME class as the probe-tighten
read that already sits beside it.

storage-split: ``universe`` stays trading-domain (``conn``); ``quote_ticks``
is written by ``QuoteTickWriter`` to the marketdata DB (``md_conn``) — the two
reads take SEPARATE connections (``universe_conn`` / ``quote_conn``), each
independently optional/fail-open so a caller with only one wired still gets
the other datum.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = ["LiquiditySnapshot", "read_liquidity_snapshot"]


@dataclass(frozen=True, slots=True)
class LiquiditySnapshot:
    """The two liquidity data points ``LiquidityProbe`` reads (either may be
    ``None`` when its source table has no row for this instrument)."""

    vol_24h_usd: float | None
    spread_bps: float | None


def read_liquidity_snapshot(
    *,
    universe_conn: sqlite3.Connection | None,
    quote_conn: sqlite3.Connection | None,
    venue: str,
    symbol: str,
    instrument_id: str,
) -> LiquiditySnapshot:
    """Read ``universe.vol_24h_usd`` (PK ``venue, symbol``) + ``quote_ticks.
    spread_bps`` (PK ``instrument_id``) — SEPARATE connections (storage-split).

    FAIL-OPEN: a ``None`` handle, a missing table, or any sqlite error leaves
    that datum ``None`` (the probe then partially/fully ABSTAINs) — this read
    must never break the tick. Two independent single-row lookups (a symbol
    can have a universe row but no quote_ticks row yet, or vice versa) so
    either datum can populate on its own, and either connection can be absent
    without losing the other.
    """
    vol_24h_usd: float | None = None
    spread_bps: float | None = None
    if universe_conn is not None:
        try:
            row = universe_conn.execute(
                "SELECT vol_24h_usd FROM universe WHERE venue = ? AND symbol = ?",
                (venue, symbol),
            ).fetchone()
            if row is not None and row[0] is not None:
                vol_24h_usd = float(row[0])
        except sqlite3.Error:
            pass
    if quote_conn is not None:
        try:
            row = quote_conn.execute(
                "SELECT spread_bps FROM quote_ticks WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()
            if row is not None and row[0] is not None:
                spread_bps = float(row[0])
        except sqlite3.Error:
            pass
    return LiquiditySnapshot(vol_24h_usd=vol_24h_usd, spread_bps=spread_bps)
