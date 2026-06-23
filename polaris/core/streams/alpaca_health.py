"""Alpaca equity feed-health gate (Jin 2026-06-22 coherence audit).

The Alpaca paper feed went 98.7h stale; with no live price an equity entry
would size/exit against a dead quote and become an unexitable zombie. This
module decides whether NEW Alpaca equity entries should be HALTED — purely as a
DATA-HEALTH gate ("no live price = cannot trade"), NOT a defensive throttle:

- It touches ZERO sizing — a healthy feed sizes/enters exactly as before.
- It is a pure function of the stored bar ts, so it AUTO-CLEARS the instant a
  fresh Alpaca bar lands (no manual reset / no sticky flag).
- It applies ONLY to Alpaca NEW entries; OKX/Capital and all EXIT paths are
  untouched (existing positions stay managed).

An operator kill-switch (``POLARIS_ALPACA_ENTRIES_HALT`` truthy) can force the
halt on regardless of feed state. The halt is OR(data_dead, operator_override).
"""

from __future__ import annotations

import os
import sqlite3
import time

# Alpaca equities are daily-bar: a healthy feed publishes ≤1 bar/day. 36h covers
# a normal cadence plus a weekend gap, yet the 98.7h dead-feed incident is far
# past it. Mirrors ``_production_bars.BAR_STALENESS_MAX_SEC`` (the recency guard
# window) so the entry halt and the per-symbol skip agree on "stale".
ALPACA_FEED_STALENESS_SEC: int = 36 * 3600

_TRUTHY = frozenset({"1", "true", "yes", "on"})

__all__ = ["ALPACA_FEED_STALENESS_SEC", "alpaca_equity_entries_halted"]


def _operator_force_halt() -> bool:
    """True iff the operator kill-switch env is truthy (explicit halt)."""
    raw = os.getenv("POLARIS_ALPACA_ENTRIES_HALT")
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


def _newest_alpaca_bar_ts(conn: sqlite3.Connection) -> int | None:
    """Newest stored Alpaca bar ts (any symbol/interval), or None if none exist.

    Bounded at ``now + slack`` so a FUTURE-dated bad bar cannot mask a dead feed
    (mirrors the trading-path ts<=now guard). The Alpaca feed health is a
    venue-wide property — one fresh symbol means the feed is alive.
    """
    now = int(time.time())
    ts_upper = now + 120  # BAR_TS_CLOCK_SKEW_SLACK_SEC
    row = conn.execute(
        "SELECT MAX(ts) FROM bars WHERE venue = 'alpaca' AND ts <= ?",
        (ts_upper,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def alpaca_equity_entries_halted(
    conn: sqlite3.Connection, *, now_ts: int | None = None
) -> bool:
    """True iff NEW Alpaca equity entries must be HALTED (dead-feed gate).

    Halt = OR of:
    - DATA-HEALTH (primary): no Alpaca bars at all, OR the newest Alpaca bar is
      older than ``ALPACA_FEED_STALENESS_SEC`` (feed dead → no live price). Pure
      function of stored data → auto-clears when fresh bars arrive.
    - OPERATOR OVERRIDE: ``POLARIS_ALPACA_ENTRIES_HALT`` truthy forces halt.

    NOT a throttle — a "cannot trade without a live price" data-health gate.
    Only NEW entries are affected; exits/management of existing positions are
    never gated here.
    """
    if _operator_force_halt():
        return True
    now = now_ts if now_ts is not None else int(time.time())
    newest = _newest_alpaca_bar_ts(conn)
    if newest is None:
        return True  # no Alpaca price at all → cannot trade
    return newest < now - ALPACA_FEED_STALENESS_SEC
