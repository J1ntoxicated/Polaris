"""Reset-only-on-ruin — per-exchange virtual-balance re-seed (Jin 2026-07-07).

Measurement hygiene, NOT a defensive throttle: this NEVER blocks/skips a
trade. If an exchange's virtual equity falls below the ruin floor
(``POLARIS_RUIN_FLOOR_FRAC`` of its $100k seed, default 0.5 -> $50k), this
logs a prominent WARNING, records an append-only ``virtual_ruin_events`` row
(exchange, ruined_ts, low_equity, week_start, reseeded_to), and signals the
caller to re-seed that exchange back to $100k so measurement continues.
flow_not_block.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Final, NamedTuple

from polaris.storage.weekly_equity_trace import week_start_ts

logger = logging.getLogger(__name__)

_ENV_RUIN_FLOOR_FRAC: Final[str] = "POLARIS_RUIN_FLOOR_FRAC"
_DEFAULT_RUIN_FLOOR_FRAC: Final[float] = 0.5


def ruin_floor_frac() -> float:
    """Fraction of seed equity below which an exchange is 'ruined' (default 0.5)."""
    raw = os.environ.get(_ENV_RUIN_FLOOR_FRAC)
    if raw is None or raw == "":
        return _DEFAULT_RUIN_FLOOR_FRAC
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_RUIN_FLOOR_FRAC


class RuinCheckResult(NamedTuple):
    """Outcome of one ``check_and_reseed_ruin`` call."""

    ruined: bool
    reseed_equity: float  # the NEW equity to apply (== current_equity when not ruined)


def _record_ruin_event(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    ruined_ts: int,
    low_equity: float,
    reseeded_to: float,
) -> None:
    conn.execute(
        "INSERT INTO virtual_ruin_events "
        "(exchange, ruined_ts, low_equity, week_start_ts, reseeded_to) "
        "VALUES (?, ?, ?, ?, ?)",
        (exchange, ruined_ts, low_equity, week_start_ts(ruined_ts), reseeded_to),
    )


def check_and_reseed_ruin(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    current_equity: float,
    seed_equity: float,
    now_ts: int,
) -> RuinCheckResult:
    """Flag + re-seed ``exchange`` if ``current_equity`` is below the ruin floor.

    Floor = ``seed_equity * ruin_floor_frac()`` (default $50k for a $100k
    seed). Below it: logs a WARNING, records a ``virtual_ruin_events`` row,
    and returns ``reseed_equity=seed_equity`` (re-seeded back to the full
    virtual start) so the caller applies it to the account. Never raises,
    never blocks a trade — pure measurement-hygiene flag + re-seed signal.
    """
    floor = seed_equity * ruin_floor_frac()
    if current_equity >= floor:
        return RuinCheckResult(ruined=False, reseed_equity=current_equity)
    logger.warning(
        "[virtual-ruin] %s virtual equity $%.2f fell below the ruin floor "
        "$%.2f (%.0f%% of $%.2f seed) — re-seeding to $%.2f (measurement "
        "hygiene, no trade blocked)",
        exchange, current_equity, floor, ruin_floor_frac() * 100.0,
        seed_equity, seed_equity,
    )
    _record_ruin_event(
        conn, exchange=exchange, ruined_ts=now_ts,
        low_equity=current_equity, reseeded_to=seed_equity,
    )
    return RuinCheckResult(ruined=True, reseed_equity=seed_equity)
