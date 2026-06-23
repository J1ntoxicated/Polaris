"""D2 — venue-native session resolver (TDD).

The session label fed to the session_mult learner must be VENUE-NATIVE, not a
single crypto-centric asia/eu/us UTC band applied to every venue:

  - OKX crypto   : 24/7 -> always one stable label ("always_on"); a crypto
                   strategy never gets pinned to an irrelevant asia floor.
  - Alpaca equity: US RTH state (rth / pre_market / after_hours / closed),
                   DST-correct via the existing us_equity_cal clock.
  - Capital CFD  : FX/indices session (open / weekend_closed), derived from the
                   venue weekend calendar, not a raw UTC hour band.

flow_not_block: the resolver returns a LABEL only — it is a learner bucket key,
never a size cut / entry block / throttle. No defensive floor anywhere here.
"""

from __future__ import annotations

import datetime as dt

import pytest

from polaris.core.sizing.session import (
    SESSION_LABELS,
    derive_session,
    resolve_venue_session,
)


def _ts(y: int, mo: int, d: int, h: int, mi: int = 0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.UTC).timestamp())


# --- OKX crypto: 24/7 -> single stable label regardless of clock -----------


@pytest.mark.parametrize(
    "ts",
    [
        _ts(2026, 6, 22, 0, 0),  # UTC midnight (asia band in derive_session)
        _ts(2026, 6, 22, 11, 0),  # eu band
        _ts(2026, 6, 22, 17, 0),  # us band
        _ts(2026, 6, 21, 3, 0),  # weekend
    ],
)
def test_okx_crypto_always_on(ts: int) -> None:
    # 24/7 venue: the venue-native session is one stable label at every hour
    # and every weekday -> the learner bucket is never split by an irrelevant
    # asia/eu/us band, never pinned to the asia floor.
    assert resolve_venue_session("okx", ts) == "always_on"


# --- Alpaca equity: real US RTH state, DST-correct -------------------------


def test_alpaca_rth_during_us_session_summer() -> None:
    # 2026-06-22 14:00 UTC = 10:00 ET (EDT) -> inside RTH [09:30,16:00) ET.
    assert resolve_venue_session("alpaca", _ts(2026, 6, 22, 14, 0)) == "rth"


def test_alpaca_pre_market() -> None:
    # 2026-06-22 12:00 UTC = 08:00 ET -> pre_market (04:00-09:30 ET).
    assert resolve_venue_session("alpaca", _ts(2026, 6, 22, 12, 0)) == "pre_market"


def test_alpaca_after_hours() -> None:
    # 2026-06-22 21:00 UTC = 17:00 ET -> after_hours (16:00-20:00 ET).
    assert resolve_venue_session("alpaca", _ts(2026, 6, 22, 21, 0)) == "after_hours"


def test_alpaca_closed_overnight() -> None:
    # 2026-06-22 02:00 UTC = 22:00 ET previous day -> closed.
    assert resolve_venue_session("alpaca", _ts(2026, 6, 22, 2, 0)) == "closed"


def test_alpaca_rth_dst_correct_winter() -> None:
    # 2026-01-15 14:00 UTC = 09:00 ET (EST) -> NOT yet RTH (pre_market). Proves
    # the resolver is DST-correct, not a fixed-UTC band.
    assert resolve_venue_session("alpaca", _ts(2026, 1, 15, 14, 0)) == "pre_market"
    # 15:00 UTC = 10:00 ET (EST) -> RTH.
    assert resolve_venue_session("alpaca", _ts(2026, 1, 15, 15, 0)) == "rth"


# --- Capital CFD: FX/indices weekend calendar ------------------------------


def test_capital_open_weekday() -> None:
    # Monday 12:00 UTC -> FX/indices market open.
    assert resolve_venue_session("capital", _ts(2026, 6, 22, 12, 0)) == "fx_open"


def test_capital_weekend_closed_saturday() -> None:
    # Saturday -> weekend closed.
    assert resolve_venue_session("capital", _ts(2026, 6, 20, 12, 0)) == "fx_weekend"


def test_capital_friday_after_close() -> None:
    # Friday 23:00 UTC (>=22:00) -> weekend closed.
    assert resolve_venue_session("capital", _ts(2026, 6, 19, 23, 0)) == "fx_weekend"


def test_capital_sunday_reopen() -> None:
    # Sunday 23:00 UTC (>=22:00) -> reopened.
    assert resolve_venue_session("capital", _ts(2026, 6, 21, 23, 0)) == "fx_open"


# --- contract: every venue resolves a NON-throttle label -------------------


def test_unknown_venue_falls_back_to_clock_band() -> None:
    # An unregistered venue degrades to the legacy crypto clock band (never an
    # error, never a block) so smoke/test paths that fabricate venues still
    # resolve a label.
    ts = _ts(2026, 6, 22, 11, 0)
    assert resolve_venue_session("mystery", ts) == derive_session(ts)


def test_resolver_label_is_just_a_string_no_size_effect() -> None:
    # The resolver returns ONLY a bucket label (str). It has no numeric/size
    # surface -> it cannot be a throttle. (flow_not_block contract.)
    for venue in ("okx", "alpaca", "capital"):
        label = resolve_venue_session(venue, _ts(2026, 6, 22, 14, 0))
        assert isinstance(label, str) and label


def test_derive_session_unchanged_for_legacy_band() -> None:
    # derive_session (the crypto UTC band) is preserved as-is for callers that
    # still want it; SESSION_LABELS unchanged.
    assert SESSION_LABELS == ("asia", "eu", "us")
    assert derive_session(_ts(2026, 6, 22, 11, 0)) == "eu"
