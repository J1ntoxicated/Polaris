"""Regime confirm = BAR-CLOSE, not tick (flip-flop fix).

Root cause (live-verified 24h: 1233 regime flip-flops): the 2-consecutive
confirm gate advanced its consecutive_count once per detect_regime_flip CALL.
detect_regime_flip runs at TICK cadence (~5-10s) inside the production loop, so
a stable candidate reached the 2-count confirm within ~10s — the hysteresis was
defeated. The fix dedups the advance on closed 5m bar identity: a candidate may
advance the consecutive_count AT MOST ONCE per closed bar.

These tests are SIGNAL-only invariants — regime never sizes/blocks/exits, so a
stabler confirm does not throttle entries (flow_not_block intact). DEMO/PAPER.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.live_recalc.regime_flip import (
    REGIME_CONFIRM_BAR_SEC,
    detect_regime_flip,
    fetch_regime,
)

BAR = REGIME_CONFIRM_BAR_SEC  # 5m in seconds


def _seed(conn: sqlite3.Connection, *, regime: str, ts: int) -> None:
    detect_regime_flip(
        conn, venue="okx", underlying_group_id="BTC", candidate=regime, now_ts=ts
    )


# ---------------------------------------------------------------------------
# 1. N ticks within ONE 5m bar advance the count by 1, not N
# ---------------------------------------------------------------------------


def test_many_ticks_one_bar_advance_count_by_one(memdb: sqlite3.Connection) -> None:
    """60 ticks at 5s spacing inside one 5m bar must NOT confirm a flip."""
    _seed(memdb, regime="bull_trend", ts=0)
    # All ticks land in bar 0 (ts < 300). 5s spacing → 60 ticks.
    last = None
    for i in range(60):
        ts = 5 + i * 5  # 5, 10, ... 300 exclusive of 300 keeps bar 0
        if ts >= BAR:
            break
        last = detect_regime_flip(
            memdb, venue="okx", underlying_group_id="BTC",
            candidate="chop", now_ts=ts,
        )
    assert last is not None
    # Count advanced exactly ONCE (to 1) despite ~59 ticks in the same bar.
    assert last.consecutive_count == 1
    assert last.confirmed is False
    # SSOT regime unchanged — still the seeded regime.
    assert fetch_regime(memdb, venue="okx", underlying_group_id="BTC") == "bull_trend"


# ---------------------------------------------------------------------------
# 2. A real bar-close advances the count → confirm on the next bar
# ---------------------------------------------------------------------------


def test_bar_close_advances_then_confirms(memdb: sqlite3.Connection) -> None:
    """Same candidate seen in two DISTINCT closed bars confirms (2 closes)."""
    _seed(memdb, regime="bull_trend", ts=0)
    # Bar 0: many ticks → count 1.
    for ts in (10, 60, 120, 280):
        d = detect_regime_flip(
            memdb, venue="okx", underlying_group_id="BTC",
            candidate="chop", now_ts=ts,
        )
    assert d.consecutive_count == 1
    assert d.confirmed is False
    # Bar 1 (ts >= 300): first tick of the new bar advances to 2 → confirm.
    d2 = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="BTC",
        candidate="chop", now_ts=BAR + 10,
    )
    assert d2.consecutive_count == 2
    assert d2.confirmed is True
    assert d2.reason == "confirmed_2x"
    assert fetch_regime(memdb, venue="okx", underlying_group_id="BTC") == "chop"


def test_second_bar_extra_ticks_do_not_double_advance(
    memdb: sqlite3.Connection,
) -> None:
    """Within bar 1, only the FIRST tick advances; later ticks in bar 1 don't."""
    _seed(memdb, regime="bull_trend", ts=0)
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="BTC", candidate="chop", now_ts=50,
    )  # bar 0 → count 1
    # bar 1, first tick advances to 2 → confirm + reset.
    d_confirm = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="BTC",
        candidate="chop", now_ts=BAR + 5,
    )
    assert d_confirm.confirmed is True
    # After confirm, chop IS the regime, so further chop ticks = no_change.
    d_after = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="BTC",
        candidate="chop", now_ts=BAR + 50,
    )
    assert d_after.reason == "no_change"


# ---------------------------------------------------------------------------
# 3. flip-flop rate drops on a seeded tick sequence
# ---------------------------------------------------------------------------


def test_flip_flop_rate_drops_on_tick_sequence(memdb: sqlite3.Connection) -> None:
    """A noisy candidate stream at TICK cadence produces far fewer confirmed
    flips with bar-close dedup than the count-every-call behavior would.

    The sequence alternates chop/bull every other tick within bars and across
    bars. Old behavior (advance-per-call) would confirm on nearly every pair of
    same-candidate ticks. Bar-close dedup requires the candidate to persist
    across two DISTINCT bars, so transient intra-bar noise cannot confirm.
    """
    _seed(memdb, regime="bull_trend", ts=0)
    confirmed_flips = 0
    # 24 bars; within each bar 12 ticks at 25s spacing. Candidate flickers
    # chop/bull_trend on alternating ticks (intra-bar noise) but the DOMINANT
    # candidate per bar only changes every 3rd bar.
    for bar in range(24):
        dominant = "chop" if (bar // 3) % 2 == 0 else "bull_trend"
        for t in range(12):
            ts = bar * BAR + t * 25
            # noise: 1 in 4 ticks proposes the opposite candidate
            cand = ("bull_trend" if dominant == "chop" else "chop") if t % 4 == 3 \
                else dominant
            d = detect_regime_flip(
                memdb, venue="okx", underlying_group_id="BTC",
                candidate=cand, now_ts=ts,
            )
            if d.confirmed:
                confirmed_flips += 1
    # 24 bars / 3-bar dominant blocks ≈ 8 regime epochs → at most a handful of
    # confirmed flips. The old per-call behavior over 288 ticks would confirm
    # dozens. Assert the dedup keeps it small.
    assert confirmed_flips <= 10
    # And it DID flip at least a couple times (it is not frozen — flow, not block).
    assert confirmed_flips >= 2


# ---------------------------------------------------------------------------
# 4. per-(venue × group) separation preserved
# ---------------------------------------------------------------------------


def test_per_venue_group_separation(memdb: sqlite3.Connection) -> None:
    """Bar-close dedup state is keyed per (venue, group) — no cross-leak."""
    _seed(memdb, regime="bull_trend", ts=0)
    detect_regime_flip(
        memdb, venue="capital", underlying_group_id="BTC",
        candidate="bull_trend", now_ts=0,
    )  # seed capital BTC
    # Advance okx/BTC toward chop in bar 0 then bar 1.
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="BTC", candidate="chop", now_ts=50,
    )
    d_okx = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="BTC",
        candidate="chop", now_ts=BAR + 10,
    )
    assert d_okx.confirmed is True
    # capital/BTC untouched by okx advance — still bull_trend.
    assert (
        fetch_regime(memdb, venue="capital", underlying_group_id="BTC")
        == "bull_trend"
    )


# ---------------------------------------------------------------------------
# 5. immediate price-crisis fast path unaffected by bar dedup
# ---------------------------------------------------------------------------


def test_price_crisis_still_immediate(memdb: sqlite3.Connection) -> None:
    """Sharp price crash flips immediately — bar dedup must not slow it."""
    _seed(memdb, regime="bull_trend", ts=0)
    d = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="BTC",
        candidate="crisis", now_ts=30, candidate_source="price",
    )
    assert d.confirmed is True
    assert d.reason == "immediate_crisis"


# ---------------------------------------------------------------------------
# 6. explicit bar_id override is honored (caller may pass closed-bar identity)
# ---------------------------------------------------------------------------


def test_explicit_bar_id_override(memdb: sqlite3.Connection) -> None:
    """Two calls with the SAME explicit bar_id advance the count only once,
    even when their now_ts values would fall in different default buckets."""
    _seed(memdb, regime="bull_trend", ts=0)
    d1 = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="BTC",
        candidate="chop", now_ts=10, bar_id=7,
    )
    assert d1.consecutive_count == 1
    d2 = detect_regime_flip(
        memdb, venue="okx", underlying_group_id="BTC",
        candidate="chop", now_ts=BAR * 5, bar_id=7,  # same bar_id, later ts
    )
    assert d2.consecutive_count == 1  # NOT advanced — same closed bar
    assert d2.confirmed is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
