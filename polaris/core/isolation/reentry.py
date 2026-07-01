"""P1 re-entry cooldown — over-trading / turnover-cost guard.

Forensic (net -$1.5K, 87% fee, SOL 20-consecutive buys at ~40s spacing):
the 5s tick fan-out re-opened the same (venue, symbol, strategy_id) every
few ticks, so fees compounded with no fresh edge. This module suppresses
*duplicate* opens inside a short window — it is **not** a defensive throttle
and does **not** shrink size or halt on P&L.

Component B (2026-05-31) replaces the old raw-strength exemption with a
**novelty** test (:func:`is_novel_reentry`): a re-entry inside the window is
exempt ONLY when the signal carries genuinely fresh info vs the LAST entry on
(venue, symbol, strategy_id) — a NEW strategy-timeframe bar
(``created_at_bar`` advanced) OR a side flip. ``strength`` is RAW MOMENTUM, not
conviction, and it spikes in the chop the bot loses in, so exempting on it was
backwards (it self-exempted every 5s tick → stacking). Genuine NEW opportunity
(new bar / side flip) still flows — blocking a same-bar re-buy of the same
thesis/side is PRECISION (surgical-strike), not a defensive throttle.

The window is now derived from the strategy timeframe (one bar:
1m→60s … 1H→3600s) via :func:`bar_seconds`, so a 1H thesis is not re-bought
every 5 minutes. The flat default below stays as the env-overridable fallback
for callers without a timeframe.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Final, Literal

logger = logging.getLogger(__name__)

__all__ = [
    "REENTRY_COOLDOWN_SEC",
    "REENTRY_STRONG_SIGNAL_STRENGTH",
    "bar_seconds",
    "concurrent_same_side_open",
    "is_novel_reentry",
    "reentry_cooldown_active",
    "stamp_reentry_anchor",
]

_ENV_COOLDOWN: Final[str] = "POLARIS_REENTRY_COOLDOWN_SEC"
_DEFAULT_COOLDOWN_SEC: Final[int] = 300  # 5min — fallback when no timeframe.

# Retained as a public constant for back-compat (telemetry / older callers); the
# Component-B entry seam no longer exempts on raw strength (see is_novel_reentry).
REENTRY_STRONG_SIGNAL_STRENGTH: Final[float] = 0.85

# Strategy-timeframe → one-bar seconds. The cooldown window is one bar of the
# emitting strategy's timeframe so a re-buy is suppressed until the next bar can
# carry fresh info (tsmom 1H → 3600s kills the 5-6min stacking).
_BAR_SECONDS: Final[dict[str, int]] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
}


def bar_seconds(timeframe: str) -> int:
    """One bar of ``timeframe`` in seconds (1m→60 … 1H→3600).

    Unknown / malformed timeframe falls back to the flat default cooldown so the
    guard never degrades to 0 (which would disable it) — fail-safe toward the
    existing 300s behaviour.
    """
    return _BAR_SECONDS.get(timeframe, _DEFAULT_COOLDOWN_SEC)


def is_novel_reentry(
    *,
    created_at_bar: int,
    side: Literal["long", "short"],
    last_entry_bar: int | None,
    last_entry_side: str | None,
) -> bool:
    """True when this signal is genuinely fresh vs the last entry on the key.

    Novelty (and ONLY novelty) exempts a re-entry from the cooldown:

    * a NEW strategy-timeframe bar — ``created_at_bar > last_entry_bar``
      (PRIMARY; within the same bar the 5s fan-out re-emits an identical bar id),
      OR
    * a side flip — ``side != last_entry_side`` (the thesis reversed).

    No prior entry on the key (``last_entry_bar is None``) → novel (first entry
    is always allowed). RAW ``strength`` is intentionally NOT consulted — it is
    momentum, not conviction, and exempting on it caused the same-bar stacking.
    """
    if last_entry_bar is None:
        return True
    if created_at_bar > last_entry_bar:
        return True
    return side != last_entry_side


def _resolve_cooldown_sec() -> int:
    raw = os.environ.get(_ENV_COOLDOWN)
    if raw is None or raw == "":
        return _DEFAULT_COOLDOWN_SEC
    try:
        return int(float(raw))
    except ValueError:
        return _DEFAULT_COOLDOWN_SEC


# Resolved at import for the module-level default; callers may pass an
# explicit ``cooldown_sec`` (the production tick reads the env each call site).
REENTRY_COOLDOWN_SEC: Final[int] = _resolve_cooldown_sec()


def stamp_reentry_anchor(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    now_ts: int,
) -> None:
    """UPSERT the persistent reject-anchor for (venue, symbol, strategy_id).

    A venue reject or a pre-submit clamp (e.g. Alpaca insufficient_buying_power)
    never writes a ``positions`` row, so :func:`reentry_cooldown_active`'s
    ``positions``-only lookup had no anchor and fell through to "allow" even
    when the in-process novelty stamp correctly marked the re-fire as NOT
    novel (PANW: 58 intents/6.1h — every reject reset the cooldown window to
    zero). Callers stamp this on EVERY submit attempt (fill or reject) so the
    window persists across a reject and a process restart. Any DB error is
    swallowed (best-effort telemetry-adjacent write — never blocks the entry
    loop the caller is already inside).
    """
    try:
        conn.execute(
            "INSERT INTO reentry_anchor (venue, symbol, strategy_id, last_ts) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(venue, symbol, strategy_id) DO UPDATE SET "
            "last_ts = excluded.last_ts",
            (venue, symbol, strategy_id, now_ts),
        )
    except sqlite3.Error as exc:  # fail-open — never block on DB trouble.
        logger.warning("reentry anchor stamp failed: %s", exc)


def reentry_cooldown_active(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    now_ts: int,
    cooldown_sec: int,
    exempt: bool,
) -> bool:
    """Return ``True`` when a new open should be skipped (cooldown in effect).

    - ``exempt=True`` (NOVELTY: new bar OR side flip — see
      :func:`is_novel_reentry`) → always ``False`` (allow entry).
    - Otherwise the cooldown anchor is ``MAX`` of the most recent ``opened_ts``
      on ``positions`` (an actual fill) AND ``last_ts`` on ``reentry_anchor``
      (a reject/clamp stamped via :func:`stamp_reentry_anchor`) for the exact
      (venue, symbol, strategy_id) key — a reject/clamp anchors the window
      exactly like a fill does, so a repeatedly-rejected signal cannot re-fire
      with zero anti-churn memory. If either exists and
      ``now_ts - anchor < cooldown_sec`` → ``True`` (skip).
    - No prior row on either table (first entry) → ``False``.
    - ``cooldown_sec <= 0`` disables the guard → ``False``.
    - Any DB error / NULL is fail-open (``False`` = allow) so the guard can
      never wedge the entry loop.
    """
    if exempt or cooldown_sec <= 0:
        return False
    try:
        row = conn.execute(
            "SELECT MAX(opened_ts) FROM positions "
            "WHERE venue = ? AND symbol = ? AND strategy_id = ?",
            (venue, symbol, strategy_id),
        ).fetchone()
    except sqlite3.Error as exc:  # fail-open — never block on DB trouble.
        logger.warning("reentry cooldown lookup failed: %s", exc)
        return False
    last_open = int(row[0]) if row is not None and row[0] is not None else None
    try:
        anchor_row = conn.execute(
            "SELECT last_ts FROM reentry_anchor "
            "WHERE venue = ? AND symbol = ? AND strategy_id = ?",
            (venue, symbol, strategy_id),
        ).fetchone()
    except sqlite3.Error as exc:  # fail-open — never block on DB trouble.
        logger.warning("reentry anchor lookup failed: %s", exc)
        anchor_row = None
    last_anchor = (
        int(anchor_row[0]) if anchor_row is not None and anchor_row[0] is not None
        else None
    )
    candidates = [v for v in (last_open, last_anchor) if v is not None]
    if not candidates:
        return False
    return (now_ts - max(candidates)) < cooldown_sec


def concurrent_same_side_open(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    side: str,
) -> bool:
    """True when a LIVE position already exists for this (key, side).

    Component B no-concurrent-duplicate guard: a time cooldown alone misses the
    12-simultaneous-BTC stacking (each clone opened on a distinct bar passes the
    novelty test), so before building the pipeline spec we also refuse a clone
    while one same-side position is still open. Counts ``positions`` rows with
    ``status NOT IN ('closed','cancelled','reconciled')`` for the exact
    (venue, symbol, strategy_id, side). One live position per name/strategy/side
    (controlled scale-in is a later component). PRECISION (surgical-strike), not
    a size dampen / P&L halt: a side flip or a different name/strategy is
    unaffected. Any DB error is fail-open (``False`` = allow) — never wedge the
    entry loop.
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM positions "
            "WHERE venue = ? AND symbol = ? AND strategy_id = ? AND side = ? "
            "AND status NOT IN ('closed','cancelled','reconciled') LIMIT 1",
            (venue, symbol, strategy_id, side),
        ).fetchone()
    except sqlite3.Error as exc:  # fail-open — never block on DB trouble.
        logger.warning("concurrent-duplicate lookup failed: %s", exc)
        return False
    return row is not None
