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
    - Otherwise look up the most recent ``opened_ts`` for the exact
      (venue, symbol, strategy_id) key (status-agnostic). If one exists and
      ``now_ts - last_open < cooldown_sec`` → ``True`` (skip).
    - No prior row (first entry) → ``False``.
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
    if row is None or row[0] is None:
        return False
    last_open = int(row[0])
    return (now_ts - last_open) < cooldown_sec


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
    ``status NOT IN ('closed','cancelled')`` for the exact
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
            "AND status NOT IN ('closed','cancelled') LIMIT 1",
            (venue, symbol, strategy_id, side),
        ).fetchone()
    except sqlite3.Error as exc:  # fail-open — never block on DB trouble.
        logger.warning("concurrent-duplicate lookup failed: %s", exc)
        return False
    return row is not None
