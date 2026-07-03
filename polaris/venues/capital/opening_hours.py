"""Per-epic Capital close-time probe — parses ``instrument.openingHours``.

Capital's ``GET /api/v1/markets/{epic}`` payload exposes a per-weekday
``openingHours`` table (``{"mon": ["HH:MM - HH:MM", ...], ..., "zone": "UTC"}``)
that the coarse asset-class calendar (``session_calendar.capital_seconds_to_close``)
throws away — lumping every index/commodity at one 21:00 UTC close. This module
recovers the PER-EPIC daily close from that table:

  * US500 (index)     mon ["00:00-21:00", "21:05-00:00"] → close 21:00 (5-min break)
  * GOLD  (commodity) mon ["00:00-20:59", "22:00-00:00"] → close 20:59 (61-min break)

The meaningful EOD close = the end of the FIRST open window followed by a real
break (a positive gap ≥ ``EOD_MIN_BREAK_MINUTES``), for a NON-FX epic. A window
ending at midnight ("00:00" → 86400) rolls continuously into the next day's
00:00 open, so it is never itself a close — the instrument trades through
midnight. True 24/5 FX (``instrument_type == "CURRENCIES"``) is EXEMPT by TYPE
(its Friday-weekly close stays owned by the legacy FX branch); the per-epic probe
returns None for it.

Pure, no I/O. The parsed week rides ``CapitalMarketConstraint.opening_hours``
(populated by the SAME ``/markets/{epic}`` fetch the constraint cache already
makes — zero new network). flow_not_block: this is additive EOD-timing
precision, NEVER a block / throttle / size-cut.

Timezone: all live fixtures carry ``"zone": "UTC"``; a present non-UTC zone →
None (fallback, no silent misinterpretation). ``now_ts`` is a UTC epoch — the
probe never reads the local (AEST) machine clock. DST: the venue republishes
``openingHours`` and the next constraint refresh (TTL 3600s) picks it up, so
per-epic hours are MORE DST-robust than the fixed legacy 21:00.
"""

from __future__ import annotations

import datetime as dt
from typing import Final

__all__ = [
    "EOD_MIN_BREAK_MINUTES",
    "WEEKDAY_LOOKAHEAD_DAYS",
    "OpeningHoursWeek",
    "parse_opening_hours",
    "seconds_to_next_close",
    "seconds_to_next_open",
]

# 7-tuple Mon..Sun; each weekday = a tuple of (start_sec, end_sec) UTC windows
# (second-of-day; an end "00:00" → 86400 = through-midnight).
OpeningHoursWeek = tuple[tuple[tuple[int, int], ...], ...]

# /debate-flag: EOD_MIN_BREAK_MINUTES — minimum gap (minutes) AFTER a window-end
# for it to count as a genuine session close vs a sub-minute data glitch. The
# conservative LOW default deliberately ADMITS the real 5-min index settlement
# break (US500/J225 21:00) and GOLD's 61-min break as daily closes, while
# rejecting absurd <2-min artifacts. It is NOT a 30-min-style threshold — that
# would mis-classify US500's 5-min break and lose its 21:00 anchor. The FX-vs-
# non-FX TYPE discriminator (``is_fx`` short-circuit), NOT the gap length, is
# what exempts true 24/5 FX; this floor is only a glitch filter. /debate item.
EOD_MIN_BREAK_MINUTES: Final[int] = 2

# Structural cap on the next-session forward day-walk (one week), not a tunable.
WEEKDAY_LOOKAHEAD_DAYS: Final[int] = 7

_SECONDS_PER_DAY: Final[int] = 86400
_WEEKDAY_KEYS: Final[tuple[str, ...]] = (
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
)


def _parse_time_to_sod(token: str, *, is_end: bool) -> int:
    """"HH:MM" / "HH:MM:SS" → second-of-day; an end "00:00" → 86400.

    Raises ``ValueError`` on a malformed token (caller maps that to None).
    """
    parts = token.strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"bad time token {token!r}")
    hh = int(parts[0])
    mm = int(parts[1])
    ss = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= hh <= 24 and 0 <= mm < 60 and 0 <= ss < 60):
        raise ValueError(f"time out of range {token!r}")
    sod = hh * 3600 + mm * 60 + ss
    if sod < 0 or sod > _SECONDS_PER_DAY:
        raise ValueError(f"sod out of range {token!r}")
    # A window END at midnight ("00:00") means it runs to end-of-day → 86400.
    if is_end and sod == 0:
        return _SECONDS_PER_DAY
    return sod


def _parse_day(windows: object) -> tuple[tuple[int, int], ...]:
    """A weekday's list of "A - B" strings → tuple of (start_sec, end_sec)."""
    if not isinstance(windows, list):
        raise ValueError("weekday windows not a list")
    out: list[tuple[int, int]] = []
    for win in windows:
        if not isinstance(win, str) or "-" not in win:
            raise ValueError(f"bad window {win!r}")
        left, right = win.split("-", 1)
        start = _parse_time_to_sod(left, is_end=False)
        end = _parse_time_to_sod(right, is_end=True)
        out.append((start, end))
    return tuple(out)


def parse_opening_hours(raw: dict[str, object]) -> OpeningHoursWeek | None:
    """Normalize the venue ``openingHours`` dict into an ``OpeningHoursWeek``.

    Returns None (→ caller uses the legacy asset-class fallback) when the table
    is absent/empty, carries a non-UTC ``zone``, is unparseable, or yields zero
    windows across the whole week.
    """
    if not raw:
        return None
    zone = raw.get("zone")
    if zone is not None and str(zone).strip().upper() != "UTC":
        return None
    try:
        week = tuple(_parse_day(raw.get(key, [])) for key in _WEEKDAY_KEYS)
    except (ValueError, TypeError):
        return None
    if not any(week):  # all 7 days empty → no usable schedule
        return None
    return week


def _inside_a_window(day: tuple[tuple[int, int], ...], sod: int) -> bool:
    """True iff ``sod`` is within some open window (end-exclusive)."""
    return any(start <= sod < end for start, end in day)


def _daily_close_sod(
    day: tuple[tuple[int, int], ...], *, min_break_minutes: int
) -> int | None:
    """The daily-close second-of-day for ``day`` — the end of the FIRST window
    followed by a break ≥ ``min_break_minutes`` (or by the overnight shut).

    A window ending at midnight (86400) is through-midnight (continuous to the
    next day's 00:00 open), so it is never itself a close and is skipped.
    """
    min_gap = min_break_minutes * 60
    n = len(day)
    for i, (_start, end) in enumerate(day):
        if end >= _SECONDS_PER_DAY:
            continue  # through-midnight window — never a close
        # gap to the next intraday window, or the overnight shut (last window).
        gap = day[i + 1][0] - end if i + 1 < n else _SECONDS_PER_DAY
        if gap >= min_gap:
            return end
    return None


def seconds_to_next_close(
    hours: OpeningHoursWeek,
    now_ts: int | float,
    *,
    min_break_minutes: int = EOD_MIN_BREAK_MINUTES,
    is_fx: bool,
) -> int | None:
    """Seconds from ``now_ts`` (UTC epoch) to THIS epic's next session close.

    Same return contract as ``capital_seconds_to_close``: a non-negative int, or
    None = no flatten boundary now (defer — never blind-flatten).

    The ONLY non-None return is when ``now`` is inside an open window: a finite
    SMALL countdown when today's close lies at/after now (genuinely approaching
    it), or a finite LARGE forward-probe value when today has no further close
    (≫ any lead window, so the caller naturally does not flatten). Outside all
    windows (weekend / break / after the close) → None.
    """
    if is_fx:
        # 24/5: the per-epic intraday micro-break is NOT a daily close; the
        # Friday weekly close stays owned by the legacy FX branch (fallback).
        return None
    try:
        ts = int(now_ts)
    except (TypeError, ValueError, OverflowError):
        return None
    now = dt.datetime.fromtimestamp(ts, tz=dt.UTC)
    wd = now.weekday()  # Mon=0 … Sun=6
    sod = now.hour * 3600 + now.minute * 60 + now.second

    # Guard FIRST (open_risks ordering): outside every open window → defer. This
    # dominates the forward-probe so a held position is never read as "near
    # close" days early.
    if not _inside_a_window(hours[wd], sod):
        return None

    close_sod = _daily_close_sod(hours[wd], min_break_minutes=min_break_minutes)
    if close_sod is not None and sod <= close_sod:
        return close_sod - sod

    # Inside a window but today's close is already past (or absent, e.g. a
    # through-midnight Sun reopen): the next close is the next session's. Walk
    # forward to the next weekday that has a daily close (finite far-future).
    for days_ahead in range(1, WEEKDAY_LOOKAHEAD_DAYS + 1):
        next_close = _daily_close_sod(
            hours[(wd + days_ahead) % 7], min_break_minutes=min_break_minutes
        )
        if next_close is not None:
            return (days_ahead * _SECONDS_PER_DAY - sod) + next_close
    return None


def seconds_to_next_open(
    hours: OpeningHoursWeek,
    now_ts: int | float,
    *,
    is_fx: bool,
) -> int | None:
    """Seconds from ``now_ts`` (UTC epoch) to THIS epic's next open window.

    Delay-route counterpart to ``seconds_to_next_close`` (P0 Capital
    timetable-aware emit wave): a venue reject during a closed window (weekend
    / daily break / after-close) resolves to a finite countdown to the NEXT
    open window instead of being retried blind or dropped — flow_not_block,
    never a permanent block.

    Return contract: None when ``now`` is ALREADY inside an open window
    (nothing to route — submit now) or when FX (24/5, exempt by type — the
    legacy Friday-weekly branch owns it) or when no open window exists
    anywhere in the ``WEEKDAY_LOOKAHEAD_DAYS`` forward walk (malformed/empty
    table — defer, never fabricate a countdown). Otherwise a non-negative int
    counting down to the next window's start second.
    """
    if is_fx:
        return None
    try:
        ts = int(now_ts)
    except (TypeError, ValueError, OverflowError):
        return None
    now = dt.datetime.fromtimestamp(ts, tz=dt.UTC)
    wd = now.weekday()  # Mon=0 … Sun=6
    sod = now.hour * 3600 + now.minute * 60 + now.second

    if _inside_a_window(hours[wd], sod):
        return None  # already open — nothing to route

    # Today may still have a LATER window (e.g. GOLD's 20:59-22:00 break).
    for start, _end in hours[wd]:
        if start >= sod:
            return start - sod

    # No more windows today — walk forward to the next weekday with one.
    for days_ahead in range(1, WEEKDAY_LOOKAHEAD_DAYS + 1):
        day = hours[(wd + days_ahead) % 7]
        if day:
            next_start = day[0][0]
            return (days_ahead * _SECONDS_PER_DAY - sod) + next_start
    return None
