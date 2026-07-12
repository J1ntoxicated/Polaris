"""Price-through shadow channel stats (maker_fill_sim R1 2026-07-12 debate).

DEMO/PAPER, display-only. Resolves ``price_through_shadow`` rows (one per
REAL entry fill, [[polaris.core.economics.price_through_shadow]]) against the
already-ingested ``bars`` AFTER each row — the SAME offline-resolution basis
``tools/visualizer/weekend_data.py``'s ``resolve_would_be_r`` established for
``weekend_shadow_orders`` — into a traded-through ratio + average price-
improvement bps reading for the SCOUT SHADOW promotion-gauge pattern
(``polaris_graph._query_shadow_channels``).

Read-only, bounded (recent rows only, short forward window) — never gates,
sizes, or throttles a trade. A missing table / empty result degrades to
``None`` fields (no fabricated ratio), matching every sibling shadow channel.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from polaris.core.economics.price_through_shadow import resolve_price_through

__all__ = ["price_through_channel_stats"]

# Recent shadow rows scanned per refresh (bounded — the caller's own TTL cache
# keeps this off the hot path; mirrors weekend_data.py's per-row bars lookup
# pattern, accepted for this exact shadow-resolution shape).
_ROW_LIMIT = 200
# Forward 1m bars looked at per row (~30 min resolution window).
_FORWARD_BAR_LIMIT = 30

_EMPTY_STATS: dict[str, Any] = {
    "n": None, "traded_through_pct": None, "avg_price_improve_bps": None,
    "fresh_ts": None,
}


def price_through_channel_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """One ``{n, traded_through_pct, avg_price_improve_bps, fresh_ts}`` reading.

    ``avg_price_improve_bps`` is averaged over the traded-through subset only
    (an unfilled row has no realised improvement to average in). A missing
    ``price_through_shadow`` / ``bars`` table degrades that ONE read to
    ``None`` fields (``sqlite3.Error`` caught) rather than raising.
    """
    try:
        rows = conn.execute(
            """SELECT venue, symbol, side, fill_px, touch_px, created_ts
               FROM price_through_shadow
               ORDER BY created_ts DESC LIMIT ?""",
            (_ROW_LIMIT,),
        ).fetchall()
    except sqlite3.Error:
        return dict(_EMPTY_STATS)
    if not rows:
        return {**_EMPTY_STATS, "n": 0}
    traded = 0
    improves: list[float] = []
    for venue, symbol, side, fill_px, touch_px, created_ts in rows:
        try:
            bar_rows = conn.execute(
                """SELECT high, low, close FROM bars
                   WHERE venue = ? AND symbol = ? AND bar_interval = '1m'
                     AND ts > ?
                   ORDER BY ts ASC LIMIT ?""",
                (venue, symbol, int(created_ts), _FORWARD_BAR_LIMIT),
            ).fetchall()
        except sqlite3.Error:
            continue
        outcome = resolve_price_through(
            side=str(side or ""), touch_px=float(touch_px), fill_px=float(fill_px),
            forward_highs=[float(r[0]) for r in bar_rows],
            forward_lows=[float(r[1]) for r in bar_rows],
            forward_closes=[float(r[2]) for r in bar_rows],
        )
        if outcome.traded_through:
            traded += 1
            improves.append(outcome.price_improve_bps)
    n = len(rows)
    return {
        "n": n,
        "traded_through_pct": round(traded / n * 100.0, 2),
        "avg_price_improve_bps": (
            round(sum(improves) / len(improves), 4) if improves else None
        ),
        "fresh_ts": int(rows[0][5]),
    }
