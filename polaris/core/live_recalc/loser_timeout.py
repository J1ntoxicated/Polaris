"""Stale-loser drift-backstop timeout — per-strategy, timeframe-scaled (core).

Moved here from ``polaris.scripts._production_recalc_exit`` to break a layer
inversion: the REPLAY engine (``polaris.core.replay.engine``) imported this helper
UP from ``polaris.scripts``. The computation is pure (deps: ``STRATEGY_REGISTRY``,
``core.isolation.reentry.bar_seconds``, ``core.live_recalc.exit_engine``, and three
env-tunable constants — all core-or-below), so it belongs in core.

``_production_recalc_exit`` re-exports ``loser_timeout_for_strategy`` (under its
historical ``_loser_timeout_for_strategy`` name) plus the two public constants,
so every existing import + the live recalc usage stays byte-for-byte identical.
Behaviour is unchanged — this is a move only; the same env vars are the SSOT.
"""

from __future__ import annotations

import os
from typing import Final

from polaris.core.isolation.reentry import bar_seconds
from polaris.core.live_recalc.exit_engine import EXIT_LOSER_TIMEOUT_SEC
from polaris.strategies import STRATEGY_REGISTRY

__all__ = [
    "EXIT_LOSER_TIMEOUT_MIN_BARS",
    "LOSER_TIMEOUT_CAP_SEC",
    "loser_timeout_for_strategy",
]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0.0 else default


# Bar-scaled floor: the stale-loser timeout is at least MIN_BARS of the strategy's
# OWN timeframe so a slow thesis (1D) is given its horizon, NOT a defensive
# throttle — the ATR-trail / MFE stops (the precise exits) are untouched.
EXIT_LOSER_TIMEOUT_MIN_BARS: int = _env_int("POLARIS_EXIT_LOSER_TIMEOUT_MIN_BARS", 2)

# Named drift-backstop CEILING (POLARIS_LOSER_TIMEOUT_SEC, default 3600s = 1hr). A
# sideways-drifting dead-thesis loser is cut faster: a slow strategy's bar-scaled
# floor is capped here so a drifter is closed at ~1hr instead of tying up capital
# for 2hr. PRECISE-EXIT loss-defense (flow_not_block): it only shortens the
# sideways-drift timeout — the G6 −1R hard rail and the ATR-trail (which cut REAL
# fast losers far earlier) are untouched.
LOSER_TIMEOUT_CAP_SEC: float = _env_float("POLARIS_LOSER_TIMEOUT_SEC", 3600.0)

# Timeframe-class boundary for the 1H drift-backstop cap ([[1d_exit_horizon_fix_
# 2026-06-26]] FIX A). The cap is scalp/1H drift logic — a strategy whose ONE bar
# is ≥ this (4H/1D/swing) is EXEMPT so its bar-scaled floor is NOT truncated to
# the 1H cap (the live bug: a 1D equity thesis force-closed at ~1hr).
_LOSER_TIMEOUT_CAP_TF_FLOOR_SEC: Final[float] = float(bar_seconds("4H"))


def loser_timeout_for_strategy(strategy_id: str) -> float:
    """Stale-loser drift-backstop timeout for ``strategy_id``.

    ``max(EXIT_LOSER_TIMEOUT_SEC, MIN_BARS × bar_seconds(timeframe))`` then, for a
    SCALP/1H-class strategy (one bar < 4H), CAPPED at the named
    ``POLARIS_LOSER_TIMEOUT_SEC`` drift backstop (default 3600s) so a dead-thesis
    drifter is cut faster instead of sitting the full 2hr. A ≥4H/1D-class strategy
    is EXEMPT from that cap (FIX A) so a daily thesis respects its own horizon
    (tsmom 1H → 3600s capped; equity 1D → 172800s floor, EOD-rail-backstopped). A
    fast strategy (1m) keeps the flat 900s. An unregistered strategy_id falls back
    to the flat default (bar_seconds fails safe to 300s for an unknown timeframe),
    also under the cap.
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return min(EXIT_LOSER_TIMEOUT_SEC, LOSER_TIMEOUT_CAP_SEC)
    bar = bar_seconds(cls.metadata.timeframe)
    tf_floor = float(EXIT_LOSER_TIMEOUT_MIN_BARS * bar)
    floored = max(EXIT_LOSER_TIMEOUT_SEC, tf_floor)
    if bar >= _LOSER_TIMEOUT_CAP_TF_FLOOR_SEC:
        return floored  # ≥4H/1D-class — exempt from the scalp/1H cap.
    return min(floored, LOSER_TIMEOUT_CAP_SEC)
