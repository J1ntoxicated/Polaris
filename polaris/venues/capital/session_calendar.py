"""Capital CFD per-asset-class EOD close calendar (deterministic, no LLM).

Capital does NOT publish a precise per-instrument next-close timestamp via REST
(unlike Alpaca's ``/v2/clock.next_close``). The per-instrument session signal it
DOES expose is ``marketStatus`` (TRADEABLE vs CLOSED/EDITS_ONLY) — but that flips
only AFTER the session has already closed, which is too late to market-flatten.

So EOD detection on Capital uses a 2-tier signal (design §per_venue_close_detection):

  (a) PRIMARY = this UTC session-calendar table mapping each Capital asset_class
      to its daily/weekly close. We flatten when ``now`` is within ``lead_sec`` of
      that close. Mirrors the ``us_equity_session_gate`` deterministic-clock
      pattern (no per-tick HTTP).
        * index / commodity → a cash-session daily close (UTC).
        * forex (FX majors) → 24/5 INTRADAY: no daily close, only the Friday
          21:00 UTC weekly close. Intraday it is EXEMPT (returns no close today).
        * equity (a Capital share CFD, rare) → US RTH 20:00 UTC close (EDT ref).

  (b) FALLBACK/confirm (handled by the caller, not here) = if the live
      ``marketStatus != 'TRADEABLE'`` the session has ALREADY closed → DEFER to
      the next-open reconcile rather than blind-queue a market order.

Timezone: close times are stored in UTC minutes-of-day. The account is AEST
(UTC+10, [[feedback_sydney_timezone]]); we keep the table in UTC and convert the
incoming epoch to its UTC weekday/minute so DST in the underlying market is the
only nuance — the index/commodity cash sessions below use a fixed UTC close that
is correct to within the lead window for the demo (learner-tunable in P1+).

This is EOD position-lifecycle discipline Jin directed (flow_not_block) — it
never blocks an entry or cuts size.
"""

from __future__ import annotations

import datetime as dt
from typing import Final

__all__ = [
    "CAPITAL_FX_WEEKLY_CLOSE_MINUTES",
    "CAPITAL_FX_WEEKLY_CLOSE_WEEKDAY",
    "CAPITAL_SESSION_CLOSE_UTC_MINUTES",
    "capital_seconds_to_close",
]

# Daily cash-session close, UTC minutes-of-day, per Capital asset_class. These
# are deliberately conservative single values (the demo does not need
# per-exchange precision); the learner network tunes them in P1+. A 'forex'
# class has NO daily close (24/5) — it is handled by the weekly-close branch.
CAPITAL_SESSION_CLOSE_UTC_MINUTES: Final[dict[str, int]] = {
    # Equity-index CFDs (US500/US tech/Wall St…) — settle at the US cash close.
    # 21:00 UTC = 16:00 ET under EDT (the bot's active half-year); a 15-min lead
    # still fires before close under EST (22:00 UTC) since the window re-checks.
    "index": 21 * 60,  # 21:00 UTC
    # Commodities (gold/oil) cash session — use the US metals/energy pit close.
    "commodity": 21 * 60,  # 21:00 UTC
    # A Capital share CFD (rare; usually routed out) — US equity RTH close.
    "equity": 20 * 60,  # 20:00 UTC (EDT reference)
}

# FX majors trade 24/5; the only hard close is the weekly Friday 21:00 UTC stop.
CAPITAL_FX_WEEKLY_CLOSE_WEEKDAY: Final[int] = 4  # Monday=0 … Friday=4
CAPITAL_FX_WEEKLY_CLOSE_MINUTES: Final[int] = 21 * 60  # 21:00 UTC Friday

# asset_class tokens that mean "FX major" (24/5, weekly-close only).
_FX_CLASSES: Final[frozenset[str]] = frozenset({"forex", "fx"})


def capital_seconds_to_close(asset_class: str, now_ts: int | float) -> int | None:
    """Seconds from ``now_ts`` (UTC epoch) to this asset_class's NEXT session close.

    Returns ``None`` when there is no close to flatten toward at ``now_ts``:
      * an unknown asset_class (no table entry, not FX) — caller treats as EXEMPT.
      * an FX major intraday (Mon-Thu, or Fri before the weekly close) — 24/5,
        no daily close, so intraday it is EXEMPT; only inside the Friday run-up to
        21:00 UTC does it return a finite countdown.

    A non-positive / past-the-close value is clamped to ``0`` (the close is at or
    behind ``now`` → fully inside the EOD window). The caller compares the result
    against its ``lead_sec`` to decide whether to flatten.
    """
    try:
        ts = int(now_ts)
    except (TypeError, ValueError, OverflowError):
        return None
    if ts < 0:
        ts = 0
    cls = asset_class.strip().lower()
    local = dt.datetime.fromtimestamp(ts, tz=dt.UTC)
    minute_of_day = local.hour * 60 + local.minute

    if cls in _FX_CLASSES:
        # 24/5: only the Friday weekly close is a real flatten boundary. On any
        # other weekday (or Fri before the close minute) there is NO daily close
        # → EXEMPT (None). On Friday at/after the close-lead we return a finite
        # countdown so the caller's lead window catches it.
        if local.weekday() != CAPITAL_FX_WEEKLY_CLOSE_WEEKDAY:
            return None
        delta = (CAPITAL_FX_WEEKLY_CLOSE_MINUTES - minute_of_day) * 60 - local.second
        if delta < 0:
            # Past Friday's close (the session is shut) — no flatten toward a
            # close in the past; the marketStatus fallback handles the shut book.
            return None
        return delta

    close_min = CAPITAL_SESSION_CLOSE_UTC_MINUTES.get(cls)
    if close_min is None:
        return None
    delta = (close_min - minute_of_day) * 60 - local.second
    if delta < 0:
        # The cash close already passed today; the next close is tomorrow's
        # session — far outside any lead window, so report "not near a close".
        return None
    return delta
