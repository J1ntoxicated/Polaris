"""TTM Squeeze release-hit-rate probe (frontgate-scan item #9, stage 2, offline).

DEMO/PAPER, virtual capital. Read-only offline digest — pairs every
``squeeze_release_bullish`` / ``squeeze_release_bearish`` ``gate_shadow_events``
row (G4, item #9's live tag) with the 1H bar canvas to check whether price
moved in the predicted direction ``FORWARD_BARS_N`` bars later. NEVER read by
any live gate/sizing/exit path; the promotion decision itself is a separate
/debate-gated step (roadmap item #9: release >= 40 events, hit-rate >= 52%).

Mirrors ``polaris.core.probes.gate_kill_value``'s read-only offline-digest
shape — only ``SELECT``, no INSERT/UPDATE/DELETE anywhere in this module.

Usage:
  python3 -m polaris.scripts.ttm_squeeze_hit_rate --db data/polaris_live.sqlite
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from typing import Final

__all__ = [
    "FORWARD_BARS_N",
    "FORWARD_BAR_INTERVAL",
    "ReleaseOutcome",
    "compute_release_hit_rate",
]

# Release forward-hit horizon: N x 1H bars (roadmap item #9 default).
FORWARD_BAR_INTERVAL: Final[str] = "1H"
FORWARD_BARS_N: Final[int] = 5

_BULLISH_FLAG: Final[str] = "squeeze_release_bullish"
_BEARISH_FLAG: Final[str] = "squeeze_release_bearish"


@dataclass(frozen=True, slots=True)
class ReleaseOutcome:
    """One resolved release event — the entry/forward 1H close + hit/miss."""

    event_id: str
    venue: str
    symbol: str
    direction: str  # "bullish" | "bearish"
    entry_price: float
    forward_price: float
    hit: bool


def _forward_bar_close(
    conn: sqlite3.Connection, *, venue: str, symbol: str, after_ts: int, n: int
) -> tuple[float, float] | None:
    """(entry_close, forward_close) — the first 1H bar at/after ``after_ts``
    is the entry mark; the Nth bar after it is the forward mark. ``None`` when
    fewer than ``n + 1`` bars exist yet (still pending / out of focus)."""
    rows = conn.execute(
        "SELECT close FROM bars WHERE instrument_id = ? AND bar_interval = ? "
        "AND ts >= ? ORDER BY ts ASC LIMIT ?",
        (f"{venue}:{symbol}", FORWARD_BAR_INTERVAL, after_ts, n + 1),
    ).fetchall()
    if len(rows) < n + 1:
        return None
    return float(rows[0][0]), float(rows[n][0])


def compute_release_hit_rate(
    conn: sqlite3.Connection, *, n: int = FORWARD_BARS_N
) -> list[ReleaseOutcome]:
    """Read-only: every resolvable squeeze-release event -> hit/miss.

    Fail-open: a missing table / any ``sqlite3.Error`` on the read yields
    ``[]``. This function only ``SELECT``s — the caller (``main``) is
    read-only too (``mode=ro`` connection).
    """
    try:
        rows = conn.execute(
            "SELECT event_id, venue, symbol, technical_flags, created_ts "
            "FROM gate_shadow_events WHERE gate_id = 4 AND "
            "(technical_flags = ? OR technical_flags = ?)",
            (_BULLISH_FLAG, _BEARISH_FLAG),
        ).fetchall()
    except sqlite3.Error:
        return []
    out: list[ReleaseOutcome] = []
    for event_id, venue, symbol, flags, created_ts in rows:
        direction = "bullish" if str(flags) == _BULLISH_FLAG else "bearish"
        marks = _forward_bar_close(
            conn, venue=str(venue), symbol=str(symbol), after_ts=int(created_ts), n=n,
        )
        if marks is None:
            continue
        entry, forward = marks
        hit = (forward > entry) if direction == "bullish" else (forward < entry)
        out.append(
            ReleaseOutcome(
                event_id=str(event_id), venue=str(venue), symbol=str(symbol),
                direction=direction, entry_price=entry, forward_price=forward, hit=hit,
            )
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite path (must exist)")
    parser.add_argument("--n", type=int, default=FORWARD_BARS_N)
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        outcomes = compute_release_hit_rate(conn, n=args.n)
        n_total = len(outcomes)
        n_hit = sum(1 for o in outcomes if o.hit)
        rate = (n_hit / n_total) if n_total else 0.0
        print(f"squeeze releases resolved: {n_total}, hits: {n_hit}, hit_rate: {rate:.3f}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
