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

from typing import Final, Literal

__all__ = [
    "PDT_DAYTRADE_THRESHOLD",
    "PDT_RANK_PENALTY_STEP",
    "RTH_CLOSE_UTC_MINUTES",
    "RTH_OPEN_UTC_MINUTES",
    "US_EQUITY_CALENDAR",
    "equity_entry_held_for_session",
    "pdt_rank_penalty",
    "stream_session_gate_active",
    "us_equity_session_state",
]

US_EQUITY_CALENDAR: Final[str] = "us_equity_cal"

# US regular trading hours in UTC minutes-since-midnight. RTH is the half-open
# window [13:30, 20:00) UTC (= 09:30-16:00 ET). Open boundary inclusive, close
# boundary exclusive. This is a deterministic UTC-clock approximation used for
# the entry-hold decision; the live venue ``/v2/clock`` (AlpacaClock.is_open,
# T9) remains the authoritative holiday-aware source and overrides at the
# adapter layer when available. The pure window is what the per-tick gate uses
# (no per-tick HTTP), matching the deterministic ``derive_session`` clock.
RTH_OPEN_UTC_MINUTES: Final[int] = 13 * 60 + 30  # 13:30 UTC
RTH_CLOSE_UTC_MINUTES: Final[int] = 20 * 60  # 20:00 UTC

# PDT (pattern-day-trader) ranking-down. >= 3 day-trades in the rolling 5-day
# window is the venue's PDT trigger. At/above this we RANK DOWN new entries
# (positive penalty) — we do NOT block. The penalty grows mildly past the
# threshold so the ranking degrades smoothly; it stays finite (never a veto).
PDT_DAYTRADE_THRESHOLD: Final[int] = 3
PDT_RANK_PENALTY_STEP: Final[float] = 1.0

SessionState = Literal["closed", "pre_market", "rth", "after_hours"]


def us_equity_session_state(ts: int | float) -> SessionState:
    """Map a UTC unix timestamp to the US-equity session state.

    Returns one of ``"closed" | "pre_market" | "rth" | "after_hours"``. RTH is
    the half-open window [13:30, 20:00) UTC. ``pre_market`` / ``after_hours``
    are the same UTC day before / after RTH; deep overnight (outside the rough
    extended-hours envelope) is ``closed``. Pure function of the UTC wall-clock
    minute; non-finite / negative input clamps to 0 (midnight UTC → closed).

    This is a deterministic clock, not a venue call. The authoritative,
    holiday-aware ``is_open`` comes from the venue ``/v2/clock`` at the adapter
    layer; this helper is the per-tick gate input (no HTTP per tick).
    """
    try:
        ts_int = int(ts)
    except (TypeError, ValueError, OverflowError):
        return "closed"
    if ts_int < 0:
        ts_int = 0
    minute_of_day = (ts_int % 86400) // 60
    if RTH_OPEN_UTC_MINUTES <= minute_of_day < RTH_CLOSE_UTC_MINUTES:
        return "rth"
    # Rough extended-hours envelope around RTH so pre/after are distinguishable
    # from deep-closed. US extended hours ~ 08:00-13:30 (pre) / 20:00-24:00
    # (after) UTC. Only RTH gates entries; pre/after/closed all HOLD entries —
    # the distinction is telemetry/observability, not a separate policy.
    if 8 * 60 <= minute_of_day < RTH_OPEN_UTC_MINUTES:
        return "pre_market"
    if RTH_CLOSE_UTC_MINUTES <= minute_of_day < 24 * 60:
        return "after_hours"
    return "closed"


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
