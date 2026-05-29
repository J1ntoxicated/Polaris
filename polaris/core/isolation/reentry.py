"""P1 re-entry cooldown — over-trading / turnover-cost guard.

Forensic (net -$1.5K, 87% fee, SOL 20-consecutive buys at ~40s spacing):
the 5s tick fan-out re-opened the same (venue, symbol, strategy_id) every
few ticks, so fees compounded with no fresh edge. This module suppresses
*duplicate* opens inside a short window — it is **not** a defensive throttle
and does **not** shrink size or halt on P&L. Strong signals are exempt so
genuine flow is preserved (AGGRESSIVE bias intact).

The cooldown window default lives here and is env-overridable; the value is
**learner-tunable in a follow-up** (Layer 5) once turnover/fee telemetry is
attributed per strategy.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Final

logger = logging.getLogger(__name__)

__all__ = [
    "REENTRY_COOLDOWN_SEC",
    "REENTRY_STRONG_SIGNAL_STRENGTH",
    "reentry_cooldown_active",
]

_ENV_COOLDOWN: Final[str] = "POLARIS_REENTRY_COOLDOWN_SEC"
_DEFAULT_COOLDOWN_SEC: Final[int] = 300  # 5min — blocks SOL ~40s 20x re-opens.

# Strong-signal exemption threshold. ``RawSignal.strength`` is clamped to
# [~0.4, 1.0] by every strategy (floor 0.4-0.6, ceil 1.0); 0.85 selects only
# top-conviction triggers, which bypass the cooldown to preserve flow.
# Learner-tunable in a follow-up alongside the window.
REENTRY_STRONG_SIGNAL_STRENGTH: Final[float] = 0.85


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

    - ``exempt=True`` (strong signal) → always ``False`` (allow entry).
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
