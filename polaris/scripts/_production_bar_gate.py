"""Bar-advance dispatch gate + idle fanout backoff (compute scheduling only).

DEMO/PAPER · AGGRESSIVE bias preserved · flow_not_block · 9-stack ban untouched.

Jin's mandate: "필요한 것만 다이나믹하게" — the 1D/1H close-only strategies (17 of
the 22 dispatch-eligible) do not need their ``generate_raw_signal`` re-invoked
every 5s tick when the underlying bar has not advanced: a strategy is a PURE
function of ``MarketView.bars`` (ADR-008 signal-generator-only role), so a
repeat call on an unchanged bar stream is a byte-identical recomputation, not a
missed opportunity. This module holds the PURE predicates only; the stateful
wiring (``ProdLoopState.last_eval_bar_ts_by_key`` /
``fanout_next_due_by_venue``) lives in ``_production_tick`` / ``_production_state``.

No signal is ever lost (no_loss_argument, see the caller's design doc): a
strategy whose input is NOT bar-only (session clock / orderbook / funding /
VIX — ``StrategyMetadata.evaluates_in_progress_bar=True``) is exempt from this
gate entirely and always re-evaluates every tick, byte-identical to before.
"""

from __future__ import annotations

import os
import sqlite3

__all__ = [
    "bar_advance_due",
    "idle_backoff_next_due",
    "idle_backoff_reset",
    "newest_bar_ts_by_venue",
    "venues_with_new_bars",
]


def newest_bar_ts_by_venue(
    conn: sqlite3.Connection, *, bar_interval: str = "1m"
) -> dict[str, int]:
    """Per-venue ``MAX(ts)`` for ``bar_interval`` — a cheap idle-activity probe.

    Uses the existing ``idx_bars_venue_symbol (venue, symbol, bar_interval,
    ts DESC)`` index (venue is the leftmost column) so this is an index scan,
    not a table scan. One indexed GROUP BY query per tick — the 1m bucket is
    already fetched every tick for the Layer 6 regime pass, so this reuses
    already-fresh data (no extra fetch).
    """
    rows = conn.execute(
        "SELECT venue, MAX(ts) FROM bars WHERE bar_interval = ? GROUP BY venue",
        (bar_interval,),
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows if r[1] is not None}


def venues_with_new_bars(
    before: dict[str, int], after: dict[str, int]
) -> set[str]:
    """Venues whose ``newest_bar_ts_by_venue`` snapshot advanced (or is new).

    A venue absent from ``before`` (first observation) counts as new-bar (no
    false idle-classification on the very first tick a venue appears).
    """
    return {
        venue
        for venue, ts in after.items()
        if venue not in before or ts > before[venue]
    }


def bar_advance_due(*, last_eval_ts: int | None, latest_bar_ts: int) -> bool:
    """True iff a close-only strategy should (re-)run ``generate_raw_signal``.

    ``last_eval_ts`` is the ts of the bar this (venue, symbol, timeframe,
    strategy) key was LAST evaluated on (``None`` = never observed — e.g. a
    fresh process start, or the first tick this key entered focus). ``None``
    always fires (no missed-bar catchup gap on restart). Otherwise fires only
    when ``latest_bar_ts`` STRICTLY advanced past the last recorded eval — a
    same-ts re-poll (bar unchanged) or a ts regression (a corrected/replaced
    bar with an older ts, which should never happen but must never spuriously
    re-fire) both skip.
    """
    if last_eval_ts is None:
        return True
    return latest_bar_ts > last_eval_ts


# ---------------------------------------------------------------------------
# spec_c — idle fanout backoff
# ---------------------------------------------------------------------------

# Env-tunable (no-hardcode mandate). Default MAX = 30s = 6x the 5s tick cadence
# — bounded at HALF the fastest native bar period (1m = 60s), so the worst-case
# idle-to-wake latency is still well inside one native bar's lifetime.
_IDLE_BACKOFF_MAX_SEC_ENV = "POLARIS_FANOUT_IDLE_BACKOFF_MAX_SEC"
_IDLE_BACKOFF_FACTOR_ENV = "POLARIS_FANOUT_IDLE_BACKOFF_FACTOR"
DEFAULT_IDLE_BACKOFF_MAX_SEC = 30.0
DEFAULT_IDLE_BACKOFF_FACTOR = 2.0
_TICK_SEC = 5.0


def _env_positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw.strip())
    except ValueError:
        return default
    return val if val > 0.0 else default


def idle_backoff_max_sec() -> float:
    """``POLARIS_FANOUT_IDLE_BACKOFF_MAX_SEC`` (default 30.0; invalid → default)."""
    return _env_positive_float(_IDLE_BACKOFF_MAX_SEC_ENV, DEFAULT_IDLE_BACKOFF_MAX_SEC)


def idle_backoff_factor() -> float:
    """``POLARIS_FANOUT_IDLE_BACKOFF_FACTOR`` (default 2.0; invalid → default)."""
    return _env_positive_float(_IDLE_BACKOFF_FACTOR_ENV, DEFAULT_IDLE_BACKOFF_FACTOR)


def idle_backoff_next_due(
    *,
    now_mono: float,
    streak: int,
    base_sec: float = _TICK_SEC,
    factor: float | None = None,
    max_sec: float | None = None,
) -> float:
    """Monotonic ts of the next allowed fanout wake for this venue.

    ``streak`` is the count of CONSECUTIVE idle ticks already observed (0 on
    the first idle tick). The interval grows ``base_sec * factor**streak``,
    capped at ``max_sec`` — never a throttle on TRADE decisions, only on HOW
    OFTEN the compute-heavy dispatch fan-out re-scans an idle venue. A
    non-positive ``factor``/``max_sec`` (bad env) degrades to the raw
    ``base_sec`` step (never a zero/negative interval — that would busy-loop).
    """
    f = factor if factor is not None and factor > 0.0 else idle_backoff_factor()
    m = max_sec if max_sec is not None and max_sec > 0.0 else idle_backoff_max_sec()
    delta = min(m, base_sec * (f ** max(0, streak)))
    return now_mono + delta


def idle_backoff_reset(
    *, bars_persisted: int, had_signal: bool, session_opened: bool
) -> bool:
    """True iff the idle streak must reset to 0 (immediate 5s-cadence resume).

    Any of: new bars ingested this tick (fresh information arrived), a strategy
    emitted a signal (dispatch was non-idle), or a venue's session just flipped
    OPEN (a fresh trading window — never stay backed-off across the edge).
    """
    return bars_persisted > 0 or had_signal or session_opened
