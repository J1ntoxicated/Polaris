"""Per-epic Capital close-time probe (DEMO/PAPER paper bot, flow_not_block).

Parses the venue ``instrument.openingHours`` (per-weekday "HH:MM - HH:MM" UTC
windows) into a normalized week, then derives "seconds to THIS epic's next
session close" — recovering the per-epic daily-close anchor the coarse
asset-class table lumps (US500 21:00 vs GOLD 20:59) — purely additive EOD-timing
precision, never a block/throttle/size-cut.

Fixtures are REAL Capital demo payload captures (tests/fixtures/capital_markets,
RO GET probe 2026-06-11). Golden assertions pin the parse; the probe cases pin
the divergence from the legacy lumped 21:00 + the None-defer contract.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import cast

import pytest

from polaris.venues.capital.opening_hours import (
    EOD_MIN_BREAK_MINUTES,
    OpeningHoursWeek,
    parse_opening_hours,
    seconds_to_next_close,
    seconds_to_next_open,
)

FIXTURES = Path(__file__).parent / "fixtures" / "capital_markets"


def _opening_hours(epic: str) -> dict[str, object]:
    body = json.loads((FIXTURES / f"{epic}.json").read_text())
    return cast("dict[str, object]", body["instrument"]["openingHours"])


def _hours(epic: str) -> OpeningHoursWeek:
    parsed = parse_opening_hours(_opening_hours(epic))
    assert parsed is not None
    return parsed


def _ts(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> int:
    return int(dt.datetime(year, month, day, hour, minute, second, tzinfo=dt.UTC).timestamp())


# ---------------------------------------------------------------------------
# parse_opening_hours — golden shape on all 6 real fixtures
# ---------------------------------------------------------------------------


def test_parse_us500_golden_week() -> None:
    """US500 indices: weekday 00:00-21:00 + 21:05-00:00; fri 00:00-21:00; sat
    closed; sun 22:00-00:00 (end 00:00 -> 86400)."""
    h = _hours("US500")
    assert len(h) == 7
    assert h[0] == ((0, 21 * 3600), (21 * 3600 + 300, 86400))  # Mon
    assert h[1] == h[0] and h[2] == h[0] and h[3] == h[0]  # Tue-Thu
    assert h[4] == ((0, 21 * 3600),)  # Fri single window
    assert h[5] == ()  # Sat closed
    assert h[6] == ((22 * 3600, 86400),)  # Sun reopen 22:00


def test_parse_gold_golden_week() -> None:
    """GOLD commodity: weekday 00:00-20:59 + 22:00-00:00 (a real 61-min break)."""
    h = _hours("GOLD")
    assert h[0] == ((0, 20 * 3600 + 59 * 60), (22 * 3600, 86400))  # Mon: close 20:59
    assert h[4] == ((0, 20 * 3600 + 59 * 60),)  # Fri
    assert h[5] == ()
    assert h[6] == ((22 * 3600, 86400),)


def test_parse_eurusd_seconds_granularity() -> None:
    """EURUSD carries an HH:MM:SS time ('00:00 - 20:59:50') — 3-component parse."""
    h = _hours("EURUSD")
    assert h[0] == ((0, 20 * 3600 + 59 * 60 + 50), (21 * 3600 + 300, 86400))


@pytest.mark.parametrize("epic", ["OIL_CRUDE", "NATURALGAS", "J225"])
def test_parse_remaining_fixtures_seven_days(epic: str) -> None:
    """Every remaining fixture parses to 7 weekday entries without error."""
    h = _hours(epic)
    assert len(h) == 7
    # OIL/NATURALGAS close 21:00 with a 60-min break; J225 close 21:00 (5-min).
    assert h[0][0][1] == 21 * 3600  # Mon first-window end = 21:00


def test_parse_absent_or_empty_returns_none() -> None:
    assert parse_opening_hours({}) is None
    assert parse_opening_hours({"zone": "UTC"}) is None  # no windows at all


def test_parse_non_utc_zone_returns_none() -> None:
    """A non-UTC zone is NOT silently reinterpreted — parse returns None so the
    caller uses the legacy fallback (defensive against a future venue payload)."""
    raw = dict(_opening_hours("US500"))
    raw["zone"] = "Europe/London"
    assert parse_opening_hours(raw) is None


def test_parse_unparseable_returns_none() -> None:
    raw = dict(_opening_hours("US500"))
    raw["mon"] = ["garbage window"]
    assert parse_opening_hours(raw) is None


# ---------------------------------------------------------------------------
# seconds_to_next_close — per-epic anchors + None-defer contract
# ---------------------------------------------------------------------------


def test_us500_mid_session_counts_to_2100() -> None:
    """US500 (index, non-FX) Mon 18:00 UTC, inside 00:00-21:00 → 3h to 21:00."""
    h = _hours("US500")
    now = _ts(2026, 6, 22, 18, 0)  # Mon
    assert seconds_to_next_close(h, now, is_fx=False) == 3 * 3600


def test_gold_per_epic_2059_not_lumped_2100() -> None:
    """GOLD pre-close Mon 20:50 → 540s to the per-epic 20:59 close (the 61-min
    20:59->22:00 break is the daily close) — PROVES divergence from legacy 21:00."""
    h = _hours("GOLD")
    now = _ts(2026, 6, 22, 20, 50)
    assert seconds_to_next_close(h, now, is_fx=False) == 9 * 60  # 20:59-20:50


def test_gold_in_break_returns_none() -> None:
    """GOLD Mon 21:30 (after 20:59 close, before 22:00 reopen) → inside NO window
    → None (defer, never blind-flatten)."""
    h = _hours("GOLD")
    now = _ts(2026, 6, 22, 21, 30)
    assert seconds_to_next_close(h, now, is_fx=False) is None


def test_weekend_saturday_returns_none() -> None:
    """Sat (hours[5]==()) → now inside no window → None."""
    h = _hours("US500")
    now = _ts(2026, 6, 27, 12, 0)  # Sat
    assert seconds_to_next_close(h, now, is_fx=False) is None


def test_sunday_before_reopen_returns_none() -> None:
    """US500 Sun 21:00 (before the 22:00 reopen) → inside no window → None."""
    h = _hours("US500")
    now = _ts(2026, 6, 28, 21, 0)  # Sun
    assert seconds_to_next_close(h, now, is_fx=False) is None


def test_sunday_after_reopen_is_finite_far_future() -> None:
    """US500 Sun 22:30 (inside 22:00-00:00) → finite forward-probe countdown to
    Mon's 21:00 close, far beyond any lead window (caller does not flatten)."""
    h = _hours("US500")
    now = _ts(2026, 6, 28, 22, 30)  # Sun
    secs = seconds_to_next_close(h, now, is_fx=False)
    assert secs is not None
    assert secs > 900  # >> EOD lead — acceptable "not near a close"
    # (86400 - 81000) + 75600 = 81000s to Mon 21:00.
    assert secs == (86400 - (22 * 3600 + 30 * 60)) + 21 * 3600


def test_fx_is_exempt_intraday() -> None:
    """EURUSD (CURRENCIES) is 24/5 → is_fx True → None; its 5-min intraday break
    must NOT resolve to a daily close (the legacy Friday-weekly branch owns FX)."""
    h = _hours("EURUSD")
    now = _ts(2026, 6, 24, 12, 0)  # Wed
    assert seconds_to_next_close(h, now, is_fx=True) is None
    # Even pre-break, FX is exempt by TYPE, not by gap length.
    assert seconds_to_next_close(h, _ts(2026, 6, 24, 20, 55), is_fx=True) is None


def test_return_contract_int_or_none() -> None:
    """Mirror capital_seconds_to_close: a non-None return is a non-negative int."""
    h = _hours("US500")
    secs = seconds_to_next_close(h, _ts(2026, 6, 22, 18, 0), is_fx=False)
    assert isinstance(secs, int) and secs >= 0


def test_min_break_default_admits_index_settlement_break() -> None:
    """The conservative EOD_MIN_BREAK_MINUTES default ADMITS US500's real 5-min
    settlement break as a daily close (a 30-min-style threshold would reject it
    and lose the 21:00 anchor) — the /debate-flagged calibration."""
    assert EOD_MIN_BREAK_MINUTES <= 5
    h = _hours("US500")
    # The 5-min break (300s) >= default floor → 21:00 is honored as the close.
    now = _ts(2026, 6, 22, 20, 55)
    assert seconds_to_next_close(h, now, is_fx=False) == 5 * 60


# ---------------------------------------------------------------------------
# seconds_to_next_open — timetable-aware delay-route counterpart (P0 Capital
# venue-reject wave). Symmetric to seconds_to_next_close: None while INSIDE an
# open window (nothing to route — the caller submits now), a non-negative
# countdown to the next window's start while OUTSIDE all windows (weekend /
# daily break / after-close) — the SG25-style "16/16 reject" case.
# ---------------------------------------------------------------------------


def test_us500_mid_session_already_open_returns_none() -> None:
    """Inside an open window → None (nothing to route, submit now)."""
    h = _hours("US500")
    now = _ts(2026, 6, 22, 18, 0)  # Mon, inside 00:00-21:00
    assert seconds_to_next_open(h, now, is_fx=False) is None


def test_gold_in_daily_break_counts_to_reopen() -> None:
    """GOLD Mon 21:30 (inside the 20:59-22:00 break) → 30min to 22:00 reopen."""
    h = _hours("GOLD")
    now = _ts(2026, 6, 22, 21, 30)
    assert seconds_to_next_open(h, now, is_fx=False) == 30 * 60


def test_sg25_weekend_closed_window_routes_to_monday_open() -> None:
    """SG25-style weekend closed-window simulation: every reject during the
    Sat/Sun dead window (index closed) resolves to a finite countdown to the
    next open window (Sun 22:00 US500 reopen) — never None-forever, never a
    block. Simulates the spec's '16/16 reject' scenario at several points
    across the closed window; each must resolve, and all must agree on the
    SAME absolute open instant (monotonic countdown)."""
    h = _hours("US500")
    sat_noon = _ts(2026, 6, 27, 12, 0)  # Sat — fully closed (h[5] == ())
    sun_morning = _ts(2026, 6, 28, 8, 0)  # Sun — before the 22:00 reopen
    target_open = _ts(2026, 6, 28, 22, 0)  # Sun 22:00 reopen (h[6])
    for probe_ts in (sat_noon, sun_morning):
        secs = seconds_to_next_open(h, probe_ts, is_fx=False)
        assert secs is not None
        assert secs == target_open - probe_ts


def test_weekend_saturday_finite_countdown_to_sunday_reopen() -> None:
    h = _hours("US500")
    now = _ts(2026, 6, 27, 12, 0)  # Sat
    secs = seconds_to_next_open(h, now, is_fx=False)
    assert secs is not None and secs > 0
    assert isinstance(secs, int)


def test_fx_is_exempt_returns_none() -> None:
    """FX (24/5) never routes — the legacy Friday-weekly branch owns it."""
    h = _hours("EURUSD")
    now = _ts(2026, 6, 27, 12, 0)  # Sat, deep in FX's weekly closure
    assert seconds_to_next_open(h, now, is_fx=True) is None


def test_no_open_window_anywhere_in_lookahead_returns_none() -> None:
    """A week with zero open windows anywhere → no next-open found → None
    (defer — the caller must NOT block forever on a malformed table, but also
    must not fabricate a bogus countdown)."""
    empty_week: OpeningHoursWeek = tuple(() for _ in range(7))  # type: ignore[assignment]
    now = _ts(2026, 6, 27, 12, 0)
    assert seconds_to_next_open(empty_week, now, is_fx=False) is None
