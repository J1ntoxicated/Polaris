"""NYSE holiday calendar: known-date fixtures against the published 2026
schedule (New Year, Jan 19 MLK, Feb 16 Presidents, Apr 3 Good Friday, May 25
Memorial, Jun 19 Juneteenth, Jul 3 Independence-observed, Sep 7 Labor,
Nov 26 Thanksgiving, Dec 25 Christmas)."""

from __future__ import annotations

from datetime import date

from tools.ops._us_market_holidays import us_market_holidays


def test_2026_known_holidays_present() -> None:
    holidays = us_market_holidays(2026)
    assert holidays == {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # MLK Day
        date(2026, 2, 16),  # Washington's Birthday
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day (Jul 4 is a Sat -> observed Fri)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    }


def test_ordinary_trading_day_not_a_holiday() -> None:
    assert date(2026, 1, 14) not in us_market_holidays(2026)  # Wed, ordinary RTH day


def test_saturday_holiday_observed_on_preceding_friday() -> None:
    assert date(2026, 7, 4) not in us_market_holidays(2026)  # actual Saturday
    assert date(2026, 7, 3) in us_market_holidays(2026)  # observed Friday
