"""Zombie-close drain — the in-session predicate must not mis-fire (P9 review).

The drain marks an un-closeable position (venue keeps rejecting the close while
the book still tracks it open) terminal, but ONLY tallies rejects that happen
while the market is OPEN. The first implementation wired the tally to the
Alpaca-only ``stream_session_gate_active``, which returns False for Capital's
``fx_indices_cal`` — so a LIVE Capital position rejected over the weekend would
have been wrongly drained. These tests pin the per-calendar dispatch so that
regression cannot return.
"""

from __future__ import annotations

import datetime as dt

from polaris.scripts._production_close import (
    ZOMBIE_CLOSE_REJECT_LIMIT,
    ZOMBIE_MIN_AGE_HOURS,
    _drain_in_session,
)

# 2026-06-06 is a Saturday; 2026-06-03 a Wednesday (UTC).
_SAT_NOON = int(dt.datetime(2026, 6, 6, 12, 0, tzinfo=dt.UTC).timestamp())
_WED_NOON = int(dt.datetime(2026, 6, 3, 12, 0, tzinfo=dt.UTC).timestamp())
_WED_PRE_RTH = int(dt.datetime(2026, 6, 3, 2, 0, tzinfo=dt.UTC).timestamp())


def test_capital_weekend_is_off_session() -> None:
    # The MIS-FIRE guard: a Capital (fx_indices_cal) close rejected over the
    # weekend must NOT count toward the drain — else a live position waiting for
    # Sunday's reopen is abandoned as a phantom zombie.
    assert _drain_in_session("capital", _SAT_NOON) is False


def test_capital_weekday_is_in_session() -> None:
    # An in-session reject (the real un-closeable-deal case) DOES count.
    assert _drain_in_session("capital", _WED_NOON) is True


def test_okx_always_on_is_always_in_session() -> None:
    assert _drain_in_session("okx", _SAT_NOON) is True
    assert _drain_in_session("okx", _WED_NOON) is True


def test_alpaca_off_rth_is_off_session() -> None:
    # us_equity_cal dispatches to the RTH gate: pre-13:30 UTC is off-session.
    assert _drain_in_session("alpaca", _WED_PRE_RTH) is False


def test_unknown_venue_counts_fail_toward_drain() -> None:
    assert _drain_in_session("nonexistent_venue", _WED_NOON) is True


def test_drain_thresholds_are_conservative() -> None:
    # Both bars must be meaningfully high so a transient cannot trip the drain.
    assert ZOMBIE_CLOSE_REJECT_LIMIT >= 20
    assert ZOMBIE_MIN_AGE_HOURS >= 1.0
