"""T13 — us_equity_cal RTH integrity gate + PDT ranking-down (NEVER a block).

Track C (Alpaca US equity) is the only stream this module touches. It is the
*consumer* of ``StreamConfig.session_calendar == "us_equity_cal"`` (the field
was carried since the T11 stream registration but was consumed nowhere). OKX
``always_on`` (track A) and Capital ``fx_indices_cal`` (track B) are NEVER
gated by this module — ``stream_session_gate_active`` returns ``False`` for
them, so A/B behaviour is byte-identical.

Two distinct, mandate-aligned decisions live here:

1. **RTH session = INTEGRITY cap (not a P&L throttle).** US regular trading
   hours are 13:30-20:00 UTC. Outside that window the equity venue rejects
   orders (market closed), so a *new* equity entry is *held* until RTH. This is
   the same class as the circuit-breaker integrity halt
   ([[feedback_circuit_breaker_philosophy]]) — it avoids a guaranteed venue
   reject, it does NOT dampen size, and it never reacts to P&L. It is NOT a
   defensive throttle and it NEVER force-closes an existing position: this
   module exposes only an entry-hold predicate; there is no close/exit/halt
   symbol in its public surface.

2. **PDT = RANKING-DOWN ONLY (never a block).** ``pdt_rank_penalty`` returns a
   finite, non-negative *ranking* number. When ``daytrade_count >= 3`` new
   day-trade-style entries get a positive penalty (lower priority in the
   existing universe/signal ranking) but are NEVER blocked — overnight holds
   are fully free, there is no P&L halt, and no entry is hard-vetoed. The
   penalty is a number the existing ranking consumes, not a veto. (flow_not_block)

NONE of this is a T4 sizing-chain multiplier: the gate decides ordering /
entry-timing, never notional. The base×continuous×tier×cell×listing×learner
chain, headroom_min(), and the 0.09 ceiling are untouched.
"""

from __future__ import annotations

import datetime as dt
from typing import Final, Literal
from zoneinfo import ZoneInfo

__all__ = [
    "PDT_DAYTRADE_THRESHOLD",
    "PDT_RANK_PENALTY_STEP",
    "RTH_CLOSE_LOCAL_MINUTES",
    "RTH_CLOSE_UTC_MINUTES",
    "RTH_OPEN_LOCAL_MINUTES",
    "RTH_OPEN_UTC_MINUTES",
    "US_EQUITY_CALENDAR",
    "WS_WARM_LEAD_MINUTES",
    "equity_entry_held_for_session",
    "equity_ws_warm_active",
    "pdt_rank_penalty",
    "stream_session_gate_active",
    "us_equity_session_state",
]

US_EQUITY_CALENDAR: Final[str] = "us_equity_cal"

# US regular trading hours, expressed in America/New_York LOCAL minutes. RTH is
# the half-open window [09:30, 16:00) ET. Open boundary inclusive, close
# boundary exclusive. ``us_equity_session_state`` converts the UTC timestamp to
# NY local time via ``zoneinfo`` so the gate is DST-correct YEAR-ROUND (the
# EST↔EDT shift moves the UTC window between 14:30-21:00 and 13:30-20:00).
#
# This is a deterministic clock (no per-tick HTTP), matching the deterministic
# ``derive_session`` clock; the live venue ``/v2/clock`` (AlpacaClock.is_open,
# T9) remains the authoritative HOLIDAY-aware source and overrides at the
# adapter layer when available.
NY_TZ: Final[ZoneInfo] = ZoneInfo("America/New_York")
RTH_OPEN_LOCAL_MINUTES: Final[int] = 9 * 60 + 30  # 09:30 ET
RTH_CLOSE_LOCAL_MINUTES: Final[int] = 16 * 60  # 16:00 ET
# Extended-hours envelope (ET local): pre-market 04:00-09:30, after-hours
# 16:00-20:00. Only RTH gates entries; pre/after/closed all HOLD — these bounds
# are telemetry/observability, not a separate policy.
PRE_MARKET_OPEN_LOCAL_MINUTES: Final[int] = 4 * 60  # 04:00 ET
AFTER_HOURS_CLOSE_LOCAL_MINUTES: Final[int] = 20 * 60  # 20:00 ET

# DST-NAIVE EDT-reference / offline-fallback constants (minutes since UTC
# midnight). These pin the RTH window AS SEEN IN EDT (summer): 13:30-20:00 UTC.
# They are NOT used by the live ``us_equity_session_state`` path (which is
# DST-correct via NY_TZ) — they remain only as a documented reference and an
# offline fallback for environments without the tz database. NOTE: in EST
# (winter) the true UTC window is 14:30-21:00, so do NOT use these for a live
# UTC comparison; convert to NY local time instead.
RTH_OPEN_UTC_MINUTES: Final[int] = 13 * 60 + 30  # 13:30 UTC (EDT reference only)
RTH_CLOSE_UTC_MINUTES: Final[int] = 20 * 60  # 20:00 UTC (EDT reference only)

# PDT (pattern-day-trader) ranking-down. >= 3 day-trades in the rolling 5-day
# window is the venue's PDT trigger. At/above this we RANK DOWN new entries
# (positive penalty) — we do NOT block. The penalty grows mildly past the
# threshold so the ranking degrades smoothly; it stays finite (never a veto).
PDT_DAYTRADE_THRESHOLD: Final[int] = 3
PDT_RANK_PENALTY_STEP: Final[float] = 1.0

# WS pre-open WARM lead (minutes). The Alpaca equity WS UNGATES this many minutes
# before the 09:30-ET open on a WEEKDAY so the socket reconnects ahead of the open
# (quotes flowing at RTH), preserving the #66 pre-open warm intent. Mirrors the
# _session_map WARM_LEAD_MIN default; env-overridable /debate calibration target.
WS_WARM_LEAD_MINUTES: Final[int] = 30

# Saturday/Sunday weekday() values (Mon=0 … Sun=6). The US cash book is shut all
# weekend, so a weekend timestamp is ALWAYS ``closed`` regardless of clock-of-day.
_WEEKEND_WEEKDAYS: Final[frozenset[int]] = frozenset({5, 6})

SessionState = Literal["closed", "pre_market", "rth", "after_hours"]


def us_equity_session_state(ts: int | float) -> SessionState:
    """Map a UTC unix timestamp to the US-equity session state (DST-correct).

    Returns one of ``"closed" | "pre_market" | "rth" | "after_hours"``. RTH is
    the half-open window [09:30, 16:00) America/New_York LOCAL time, so it is
    correct YEAR-ROUND: the timestamp is converted to NY local time via
    ``zoneinfo`` (EST/EDT applied automatically). ``pre_market`` (04:00-09:30
    ET) / ``after_hours`` (16:00-20:00 ET) bracket RTH; outside that envelope is
    ``closed``. Non-finite / negative input clamps to epoch (→ closed).

    🔴 WEEKEND-AWARE: the cash book is shut ALL weekend, so a Saturday/Sunday
    timestamp is ALWAYS ``closed`` — even at a clock-of-day that WOULD be RTH on
    a weekday (Sat 10:08 ET). Before this guard the clock was a pure time-of-day
    map: it read 'rth' on Sat ET-RTH-hours, so the WS gate (``_gated_at``) and
    the entry-hold gate (``equity_entry_held_for_session``) — both of which read
    this SSOT — were wrongly OPEN on the weekend (the idle-reconnect loop +
    weekend-entry buying-power drain). This single guard shuts both gaps.

    This is a deterministic clock, not a venue call. The authoritative,
    holiday-aware ``is_open`` comes from the venue ``/v2/clock`` at the adapter
    layer (the weekday HOLIDAY case); this helper is the per-tick gate input (no
    HTTP per tick), and the weekday-holiday override stays at the adapter layer.
    """
    try:
        ts_int = int(ts)
    except (TypeError, ValueError, OverflowError):
        return "closed"
    if ts_int < 0:
        ts_int = 0
    local = dt.datetime.fromtimestamp(ts_int, tz=NY_TZ)
    if local.weekday() in _WEEKEND_WEEKDAYS:
        return "closed"  # cash book shut all weekend — never RTH/pre/after
    minute_of_day = local.hour * 60 + local.minute
    if RTH_OPEN_LOCAL_MINUTES <= minute_of_day < RTH_CLOSE_LOCAL_MINUTES:
        return "rth"
    # Extended-hours envelope (ET local) so pre/after are distinguishable from
    # deep-closed. Only RTH gates entries; pre/after/closed all HOLD entries —
    # the distinction is telemetry/observability, not a separate policy.
    if PRE_MARKET_OPEN_LOCAL_MINUTES <= minute_of_day < RTH_OPEN_LOCAL_MINUTES:
        return "pre_market"
    if RTH_CLOSE_LOCAL_MINUTES <= minute_of_day < AFTER_HOURS_CLOSE_LOCAL_MINUTES:
        return "after_hours"
    return "closed"


def equity_ws_warm_active(ts: int | float) -> bool:
    """Is ``ts`` inside the Alpaca equity WS pre-open WARM lead? (weekday only).

    ``True`` in the half-open window ``[09:30 ET - WS_WARM_LEAD_MINUTES, 09:30 ET)``
    on a WEEKDAY — i.e. the socket should reconnect AHEAD of the open so quotes
    are flowing at RTH (the #66 pre-open warm intent, applied to the realtime WS).
    ``False`` on the weekend (cash book shut), deep overnight, during RTH itself
    (``us_equity_session_state`` handles RTH), and after-hours.

    Pure deterministic clock (NY-local, DST-correct), no HTTP. This is a WS-gate
    helper ONLY — it never touches entry / sizing / exit (those use the session
    state / entry-hold). flow_not_block: a warm reconnect ENABLES early data, it
    is not a throttle. Non-finite / negative input degrades to ``False`` (no warm
    on doubt → the gate simply waits for RTH).
    """
    try:
        ts_int = int(ts)
    except (TypeError, ValueError, OverflowError):
        return False
    if ts_int < 0:
        return False
    local = dt.datetime.fromtimestamp(ts_int, tz=NY_TZ)
    if local.weekday() in _WEEKEND_WEEKDAYS:
        return False  # weekend → no cash open to warm toward
    minute_of_day = local.hour * 60 + local.minute
    warm_start = RTH_OPEN_LOCAL_MINUTES - WS_WARM_LEAD_MINUTES
    return warm_start <= minute_of_day < RTH_OPEN_LOCAL_MINUTES


def equity_entry_held_for_session(ts: int | float) -> bool:
    """Whether a NEW equity entry must be HELD at ``ts`` (integrity, not P&L).

    ``True`` outside RTH (pre_market / after_hours / closed): the venue would
    reject the order (market closed), so we hold the new entry until RTH. This
    is an integrity constraint (avoids a guaranteed venue reject), NOT a
    defensive size throttle and NOT a P&L halt. ``False`` during RTH.

    This predicate decides only NEW entries. It has no power to close or exit
    an existing position — held positions ride through the closed window
    untouched (overnight holds are fully free).
    """
    return us_equity_session_state(ts) != "rth"


def pdt_rank_penalty(daytrade_count: int) -> float:
    """Ranking-down penalty for the PDT state — NEVER a block (flow_not_block).

    Returns a finite, non-negative *ranking* number. Below the PDT threshold
    (``daytrade_count < 3``) the penalty is ``0.0`` (no effect). At/above the
    threshold the penalty is positive and grows one ``PDT_RANK_PENALTY_STEP``
    per day-trade past the threshold edge, so a day-trade-style entry sinks in
    the existing universe/signal ranking but is STILL eligible. The penalty is
    always finite — there is no infinite/sentinel value that would amount to a
    hard veto. Overnight holds are unaffected (this hooks into entry ranking,
    not position holding); there is no P&L halt and no entry hard-block.

    The consumer is the existing universe/signal ranking (``focus_rank`` /
    signal priority): callers add this penalty to lower a candidate's priority.
    It is NOT a T4 sizing multiplier — it never touches notional.
    """
    try:
        n = int(daytrade_count)
    except (TypeError, ValueError):
        return 0.0
    if n < PDT_DAYTRADE_THRESHOLD:
        return 0.0
    return float(n - PDT_DAYTRADE_THRESHOLD + 1) * PDT_RANK_PENALTY_STEP


def stream_session_gate_active(session_calendar: str) -> bool:
    """Whether the RTH/PDT gate applies to a stream's ``session_calendar``.

    Only ``us_equity_cal`` (Track C / Alpaca equity) is gated. ``always_on``
    (OKX, track A) and ``fx_indices_cal`` (Capital, track B) return ``False`` —
    they are NEVER touched by this gate, so A/B behaviour is byte-identical.
    """
    return session_calendar == US_EQUITY_CALENDAR
