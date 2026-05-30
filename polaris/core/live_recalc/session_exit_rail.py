"""Gate architecture Phase 3 — per-stream session-forced-exit RAIL (G7).

CALENDAR INTEGRITY, **not** a P&L throttle. This is a per-stream RAIL on TOP of
the shared #26 precise-exit FSM / G6 stop / G7 widening — those stay unchanged.
The rail only ADDS a calendar-driven ``EXIT_NOW`` when the venue's session /
weekend close is *imminent* (within ``CLOSE_BUFFER`` minutes), because the
position cannot be SAFELY held across that close: holding over a closed market =
gap risk + rollover cost the venue position cannot manage. It fires on TIME
ONLY — never on a loss limit, drawdown, or any P&L condition — exactly the same
class as the existing integrity gates ([[feedback_circuit_breaker_philosophy]]
integrity-only). It is NOT a defensive size-cut, NOT an entry-block, and there
is NO P&L halt. AGGRESSIVE bias intact: winners RUN until the calendar forces
flat (venue reality, not a throttle).

Per-stream behaviour, keyed on ``StreamProfile.session_calendar``:

- ``always_on`` (A — OKX crypto, 24/7): the rail NEVER fires → A is
  BYTE-IDENTICAL (there is no hard calendar close for crypto).
- ``fx_indices_cal`` (B — Capital CFD): FX/indices trade ~24/5; the hard close
  is the WEEKEND (Friday 22:00 UTC — the same boundary the existing
  ``_stream_guards.cfd_market_open_at`` clock uses). When that close is within
  CLOSE_BUFFER the rail forces a flat. Reuses that weekend boundary; it does NOT
  reimplement a calendar.
- ``us_equity_cal`` (C — Alpaca equity): no-overnight. When the RTH close
  (16:00 ET, DST-correct — the same boundary the existing
  ``equity_session_gate.us_equity_session_state`` clock uses) is within
  CLOSE_BUFFER the rail forces a flat. Reuses that RTH clock.

``CLOSE_BUFFER`` (minutes before the close to flatten) is env-overridable
(``POLARIS_SESSION_CLOSE_BUFFER_MIN``), with a CONSERVATIVE default (10 min —
flatten only when the close is imminent) and is **FLAGGED as a pending /debate
calibration param**.

Pure + unit-testable: no I/O, no venue call. The recalc tick reads the
position's ``session_calendar`` and ``now_ts``, calls
:func:`session_forced_exit`, and routes a fired close through the existing
``close_specific_position`` (the close path is unchanged — this only DECIDES).
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from typing import Final
from zoneinfo import ZoneInfo

__all__ = [
    "SESSION_CLOSE_BUFFER_ENV",
    "SESSION_CLOSE_BUFFER_MIN",
    "SessionExitDecision",
    "session_forced_exit",
]

# --- session_calendar tokens (mirror StreamConfig.session_calendar) ---------
_CAL_ALWAYS_ON: Final[str] = "always_on"
_CAL_FX_INDICES: Final[str] = "fx_indices_cal"
_CAL_US_EQUITY: Final[str] = "us_equity_cal"

# --- venue close boundaries (REUSE the existing deterministic clocks) -------
# B (fx_indices_cal): the FX/indices weekend close is Friday 22:00 UTC — the
# EXACT boundary _stream_guards.cfd_market_open_at uses (open Sun 22:00 UTC ->
# Fri 22:00 UTC). FX is continuous through the week, so the only hard close that
# forces a flat is this weekend boundary.
_FX_FRIDAY_WEEKDAY: Final[int] = 4  # Mon=0 .. Fri=4
_FX_WEEKEND_CLOSE_HOUR_UTC: Final[int] = 22  # Friday 22:00 UTC

# C (us_equity_cal): RTH close is 16:00 America/New_York LOCAL (DST-correct) —
# the EXACT close boundary us_equity_session_state uses ([09:30, 16:00) ET).
_NY_TZ: Final[ZoneInfo] = ZoneInfo("America/New_York")
_RTH_CLOSE_LOCAL_MINUTES: Final[int] = 16 * 60  # 16:00 ET
_RTH_OPEN_LOCAL_MINUTES: Final[int] = 9 * 60 + 30  # 09:30 ET


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# --- CLOSE_BUFFER — conservative default, env-overridable, FLAG for /debate --
SESSION_CLOSE_BUFFER_ENV: Final[str] = "POLARIS_SESSION_CLOSE_BUFFER_MIN"
SESSION_CLOSE_BUFFER_MIN: Final[float] = _env_float(SESSION_CLOSE_BUFFER_ENV, 10.0)


@dataclass(slots=True, frozen=True)
class SessionExitDecision:
    """Outcome of one session-rail evaluation (close-or-hold of THIS position).

    ``close`` True → a calendar-forced flat must fire (route through the existing
    ``close_specific_position``). ``close_reason`` names the calendar that fired
    (``session_close_<calendar>``) so telemetry can distinguish a calendar flat
    from a #26 precise exit. TIME-only — never set on a P&L condition.
    """

    close: bool
    close_reason: str | None = None


def _utc(ts: int | float) -> dt.datetime:
    try:
        ts_int = int(ts)
    except (TypeError, ValueError, OverflowError):
        ts_int = 0
    if ts_int < 0:
        ts_int = 0
    return dt.datetime.fromtimestamp(ts_int, tz=dt.UTC)


def _minutes_to_fx_weekend_close(ts: int | float) -> float | None:
    """Minutes until the FX/indices weekend close (Friday 22:00 UTC).

    Returns ``None`` when the market is NOT in the pre-close approach on the
    SAME Friday session day (i.e. it is not Friday, or it is Friday already at/
    after 22:00 UTC). A negative/None result means the rail does not arm — the
    rail is a *pre-close* flatten while still in-session, never a re-fire while
    already closed. Mirrors _stream_guards.cfd_market_open_at's Friday boundary.
    """
    d = _utc(ts)
    if d.weekday() != _FX_FRIDAY_WEEKDAY:
        return None
    close = d.replace(
        hour=_FX_WEEKEND_CLOSE_HOUR_UTC, minute=0, second=0, microsecond=0
    )
    if d >= close:
        return None  # already at/after the weekend close — nothing to pre-empt
    return (close - d).total_seconds() / 60.0


def _minutes_to_rth_close(ts: int | float) -> float | None:
    """Minutes until the US-equity RTH close (16:00 ET, DST-correct).

    Returns ``None`` when NOT currently inside RTH (the rail is a pre-close
    flatten while still in-session; an already-closed/overnight position is the
    #26 FSM / G6 concern, not this rail). Mirrors the 16:00-ET close boundary of
    us_equity_session_state (which uses the same America/New_York local clock).
    """
    local = _utc(ts).astimezone(_NY_TZ)
    minute_of_day = local.hour * 60 + local.minute
    # RTH is [09:30, 16:00) ET; only inside RTH can a close be "imminent".
    if not (_RTH_OPEN_LOCAL_MINUTES <= minute_of_day < _RTH_CLOSE_LOCAL_MINUTES):
        return None
    return float(_RTH_CLOSE_LOCAL_MINUTES - minute_of_day)


def session_forced_exit(
    session_calendar: str,
    now_ts: int | float,
    *,
    pnl_r: float = 0.0,
    close_buffer_min: float | None = None,
) -> SessionExitDecision:
    """Per-stream session-close RAIL — decide a calendar-forced flat (TIME-only).

    Keyed on ``session_calendar`` (resolved once from the stream, Phase 0 seam):

    - ``always_on`` (A): NEVER fires → byte-identical (no calendar close).
    - ``fx_indices_cal`` (B): force flat when the weekend close (Friday 22:00
      UTC) is within ``close_buffer_min``.
    - ``us_equity_cal`` (C): force flat when the RTH close (16:00 ET) is within
      ``close_buffer_min`` (no-overnight).
    - any other token: NEVER fires (safe default).

    ``pnl_r`` is ACCEPTED but DELIBERATELY UNUSED in the decision — the rail
    fires on TIME ONLY, never on a loss / drawdown. It is present so callers can
    pass it uniformly (and so the TIME-not-PnL invariant is explicit + tested):
    a deep winner flattens at the close (venue reality), a mid-session loser is
    NOT force-closed by THIS rail (the #26 FSM / G6 stop owns that).

    ``close_buffer_min`` overrides ``SESSION_CLOSE_BUFFER_MIN`` (the
    env-overridable, /debate-FLAGGED default) for tests / per-call calibration.
    """
    del pnl_r  # TIME-only — pnl is never a condition for THIS rail.
    buffer_min = (
        SESSION_CLOSE_BUFFER_MIN if close_buffer_min is None else float(close_buffer_min)
    )
    cal = (session_calendar or "").strip().lower()

    if cal == _CAL_ALWAYS_ON:
        # A — OKX crypto, 24/7: no calendar close exists → byte-identical.
        return SessionExitDecision(close=False)

    if cal == _CAL_FX_INDICES:
        mins = _minutes_to_fx_weekend_close(now_ts)
    elif cal == _CAL_US_EQUITY:
        mins = _minutes_to_rth_close(now_ts)
    else:
        # Unknown / unmapped calendar — never fire (no defined close boundary).
        return SessionExitDecision(close=False)

    if mins is None or mins > buffer_min:
        return SessionExitDecision(close=False)
    return SessionExitDecision(close=True, close_reason=f"session_close_{cal}")
