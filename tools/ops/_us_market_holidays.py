"""Static NYSE full-day-closure holiday calendar (deterministic, stdlib-only).

``polaris.core.sessions.equity_session_gate.us_equity_session_state`` is
weekend-aware but explicitly NOT holiday-aware by design (the adapter-layer
venue ``/v2/clock`` owns that). ``tools/ops/log_sentry.py`` is a read-only,
no-network sentry, so it cannot call the live venue clock — this module fills
that gap with a static, computed calendar (no external package; a prior
exchange-holiday-calendar deferral is noted in ``polaris/scripts/sentinel.py``)
so a weekday-holiday RTH-hours window doesn't false-WARN SESSION_SILENT_EQUITY
(2026-07-12 log_sentry review — same bug class as the 2026-06-28 weekend-STALL).
"""

from __future__ import annotations

from datetime import date, timedelta


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th (1-indexed) occurrence of `weekday` (Mon=0) in `month`/`year`."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last occurrence of `weekday` (Mon=0) in `month`/`year`."""
    next_month_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = next_month_first - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher algorithm, stdlib-only)."""
    a = year % 19
    b, c = divmod(year, 100)
    d4, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d4 - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(d: date) -> date:
    """NYSE observed-date rule: Sat -> observed Fri, Sun -> observed Mon."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def us_market_holidays(year: int) -> frozenset[date]:
    """NYSE full-day-closure holidays for `year` (observed dates)."""
    return frozenset(
        {
            _observed(date(year, 1, 1)),  # New Year's Day
            _nth_weekday(year, 1, 0, 3),  # MLK Day
            _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
            _easter_sunday(year) - timedelta(days=2),  # Good Friday
            _last_weekday(year, 5, 0),  # Memorial Day
            _observed(date(year, 6, 19)),  # Juneteenth
            _observed(date(year, 7, 4)),  # Independence Day
            _nth_weekday(year, 9, 0, 1),  # Labor Day
            _nth_weekday(year, 11, 3, 4),  # Thanksgiving
            _observed(date(year, 12, 25)),  # Christmas
        }
    )
