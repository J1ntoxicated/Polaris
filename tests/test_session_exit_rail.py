"""Gate architecture Phase 3 — G7 per-stream session-forced-exit RAIL.

POLICY (the load-bearing invariants under test — CALENDAR INTEGRITY, NOT a P&L
throttle):

- The rail fires on TIME ONLY: the venue's session / weekend close is imminent
  (within ``CLOSE_BUFFER`` minutes), so the position cannot be SAFELY held
  across the close (gap risk + rollover cost the venue position cannot manage).
  Same class as the circuit-breaker integrity halt
  ([[feedback_circuit_breaker_philosophy]] integrity-only). It is NOT a
  defensive size-cut, NOT an entry-block, and there is NO P&L / drawdown halt.
- A (OKX crypto, ``always_on``, 24/7): the rail NEVER fires — A is
  byte-identical (no hard calendar close exists for crypto).
- B (Capital CFD, ``fx_indices_cal``): FX/indices trade ~24/5; the hard close
  is the weekend (Friday 22:00 UTC). When that close is within CLOSE_BUFFER the
  rail forces a flat (no holding over a closed market). Not before.
- C (Alpaca equity, ``us_equity_cal``): no-overnight — when the RTH close
  (16:00 ET, DST-correct) is within CLOSE_BUFFER the rail forces a flat. Not
  mid-RTH.
- TIME not PnL: a deep WINNER on B/C still flattens at the close (venue
  reality); a mid-session LOSER is NOT force-closed by THIS rail (the #26 FSM /
  G6 stop owns that). The rail's decision is independent of pnl_r.
"""

from __future__ import annotations

import datetime as dt

from polaris.core.live_recalc.session_exit_rail import (
    SESSION_CLOSE_BUFFER_ENV,
    SESSION_CLOSE_BUFFER_MIN,
    session_forced_exit,
)


def _utc_ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    base = dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC)
    return int(base.timestamp())


# A reference Friday + a weekday + a Wednesday for the FX clock. 2026-05-29 is a
# Friday (weekday()==4); 2026-05-27 is a Wednesday.
_FRIDAY = (2026, 5, 29)
_WEDNESDAY = (2026, 5, 27)


# ---------------------------------------------------------------------------
# A — always_on (OKX crypto): the rail NEVER fires (byte-identical, 24/7).
# ---------------------------------------------------------------------------


def test_always_on_never_fires_midweek() -> None:
    dec = session_forced_exit("always_on", _utc_ts(*_WEDNESDAY, 12, 0))
    assert dec.close is False
    assert dec.close_reason is None


def test_always_on_never_fires_at_fx_weekend_close() -> None:
    """Even at the exact FX weekend-close instant, always_on does NOT flatten —
    crypto is 24/7, there is no calendar close for A."""
    dec = session_forced_exit("always_on", _utc_ts(*_FRIDAY, 21, 55))
    assert dec.close is False


def test_always_on_never_fires_at_us_rth_close() -> None:
    """Even right before the US RTH close, always_on does NOT flatten."""
    dec = session_forced_exit("always_on", _utc_ts(2026, 7, 15, 19, 55))
    assert dec.close is False


def test_always_on_pnl_irrelevant() -> None:
    """A deep winner OR a deep loser on always_on still never fires (TIME-only,
    and A has no calendar)."""
    ts = _utc_ts(*_FRIDAY, 21, 59)
    assert session_forced_exit("always_on", ts, pnl_r=99.0).close is False
    assert session_forced_exit("always_on", ts, pnl_r=-99.0).close is False


# ---------------------------------------------------------------------------
# B — fx_indices_cal (Capital CFD): force flat when the weekend close (Friday
#     22:00 UTC) is within CLOSE_BUFFER; NOT before.
# ---------------------------------------------------------------------------


def test_fx_forced_flat_just_before_weekend_close() -> None:
    """Friday 21:55 UTC (5 min before the 22:00 UTC weekend close) is within the
    default 10-min buffer → force flat."""
    dec = session_forced_exit("fx_indices_cal", _utc_ts(*_FRIDAY, 21, 55))
    assert dec.close is True
    assert dec.close_reason == "session_close_fx_indices_cal"


def test_fx_forced_flat_at_buffer_edge() -> None:
    """Exactly CLOSE_BUFFER minutes before close (21:50 with a 10-min buffer) is
    inside the half-open flatten window."""
    dec = session_forced_exit(
        "fx_indices_cal", _utc_ts(*_FRIDAY, 21, 50), close_buffer_min=10
    )
    assert dec.close is True


def test_fx_not_flat_before_buffer() -> None:
    """Friday 21:40 UTC (20 min before close) is OUTSIDE a 10-min buffer → hold;
    the #26 FSM / stop still owns mid-session exits."""
    dec = session_forced_exit(
        "fx_indices_cal", _utc_ts(*_FRIDAY, 21, 40), close_buffer_min=10
    )
    assert dec.close is False


def test_fx_not_flat_midweek() -> None:
    """A Wednesday midday — FX market is open, no close imminent → hold."""
    dec = session_forced_exit("fx_indices_cal", _utc_ts(*_WEDNESDAY, 12, 0))
    assert dec.close is False


def test_fx_not_flat_already_closed_weekend() -> None:
    """Saturday — market already closed; the rail is a *pre-close* flatten, not a
    re-fire while closed (no open position should exist, and there is nothing to
    pre-empt). Hold (False)."""
    saturday = _utc_ts(2026, 5, 30, 12, 0)  # 2026-05-30 is a Saturday
    dec = session_forced_exit("fx_indices_cal", saturday)
    assert dec.close is False


def test_fx_buffer_does_not_bleed_into_prior_day() -> None:
    """A Thursday at 21:55 UTC is NOT a weekend-close pre-flatten (Thu->Fri the
    market stays open) → hold. Only Friday's 22:00 UTC close arms the rail."""
    thursday = _utc_ts(2026, 5, 28, 21, 55)  # 2026-05-28 is a Thursday
    dec = session_forced_exit("fx_indices_cal", thursday)
    assert dec.close is False


# ---------------------------------------------------------------------------
# C — us_equity_cal (Alpaca equity): no-overnight. Force flat when the RTH close
#     (16:00 ET, DST-correct) is within CLOSE_BUFFER; NOT mid-RTH.
# ---------------------------------------------------------------------------


def test_equity_forced_flat_just_before_rth_close_edt() -> None:
    """July (EDT): RTH close 16:00 ET == 20:00 UTC. 19:55 UTC (5 min before) is
    within the default 10-min buffer → force flat (no overnight)."""
    dec = session_forced_exit("us_equity_cal", _utc_ts(2026, 7, 15, 19, 55))
    assert dec.close is True
    assert dec.close_reason == "session_close_us_equity_cal"


def test_equity_forced_flat_just_before_rth_close_est() -> None:
    """January (EST): RTH close 16:00 ET == 21:00 UTC. 20:55 UTC (5 min before)
    is within the buffer → force flat (DST-correct)."""
    dec = session_forced_exit("us_equity_cal", _utc_ts(2026, 1, 15, 20, 55))
    assert dec.close is True


def test_equity_not_flat_mid_rth() -> None:
    """Mid-RTH (17:00 UTC EDT == 13:00 ET, well before the 16:00 ET close) → hold.
    The #26 FSM / G6 stop owns mid-RTH exits, not this rail."""
    dec = session_forced_exit("us_equity_cal", _utc_ts(2026, 7, 15, 17, 0))
    assert dec.close is False


def test_equity_not_flat_before_buffer() -> None:
    """July: 19:40 UTC (20 min before the 20:00 UTC close) is OUTSIDE a 10-min
    buffer → hold."""
    dec = session_forced_exit(
        "us_equity_cal", _utc_ts(2026, 7, 15, 19, 40), close_buffer_min=10
    )
    assert dec.close is False


def test_equity_not_flat_when_already_closed() -> None:
    """Outside RTH (overnight) the rail does not fire — a held position over the
    closed window is the #26/G6 concern; the rail is a *pre-close* flatten while
    still in-session."""
    dec = session_forced_exit("us_equity_cal", _utc_ts(2026, 7, 15, 3, 0))
    assert dec.close is False


# ---------------------------------------------------------------------------
# TIME not PnL — the rail's decision is independent of pnl_r.
# ---------------------------------------------------------------------------


def test_deep_winner_b_still_flattens_at_close() -> None:
    """A deep WINNER on B still flattens at the weekend close (venue reality —
    winners run UNTIL the calendar forces flat, which is not a throttle)."""
    dec = session_forced_exit(
        "fx_indices_cal", _utc_ts(*_FRIDAY, 21, 55), pnl_r=12.0
    )
    assert dec.close is True


def test_deep_winner_c_still_flattens_at_close() -> None:
    dec = session_forced_exit(
        "us_equity_cal", _utc_ts(2026, 7, 15, 19, 55), pnl_r=12.0
    )
    assert dec.close is True


def test_mid_session_loser_b_not_force_closed_by_rail() -> None:
    """A mid-session LOSER on B is NOT force-closed by THIS rail (no close
    imminent) — the #26 FSM / stop handles a losing mid-session position. The
    rail is TIME-only, never a loss/drawdown reaction."""
    dec = session_forced_exit(
        "fx_indices_cal", _utc_ts(*_WEDNESDAY, 12, 0), pnl_r=-5.0
    )
    assert dec.close is False


def test_mid_session_loser_c_not_force_closed_by_rail() -> None:
    dec = session_forced_exit(
        "us_equity_cal", _utc_ts(2026, 7, 15, 17, 0), pnl_r=-5.0
    )
    assert dec.close is False


def test_winner_and_loser_identical_at_close_for_b() -> None:
    """Same instant, opposite PnL → identical rail decision (TIME-only)."""
    ts = _utc_ts(*_FRIDAY, 21, 55)
    assert (
        session_forced_exit("fx_indices_cal", ts, pnl_r=20.0).close
        == session_forced_exit("fx_indices_cal", ts, pnl_r=-20.0).close
    )


# ---------------------------------------------------------------------------
# CLOSE_BUFFER — env-overridable, conservative default, FLAGGED for /debate.
# ---------------------------------------------------------------------------


def test_close_buffer_default_is_conservative() -> None:
    """The default buffer is a small, conservative last-N-minutes window
    (flatten only when the close is imminent)."""
    assert SESSION_CLOSE_BUFFER_MIN == 10.0


def test_close_buffer_env_name() -> None:
    assert SESSION_CLOSE_BUFFER_ENV == "POLARIS_SESSION_CLOSE_BUFFER_MIN"


def test_close_buffer_env_override(monkeypatch) -> None:  # noqa: ANN001
    """A wider env buffer arms the rail earlier (30 min before close fires with a
    30-min buffer but not with the 10-min default)."""
    ts = _utc_ts(2026, 7, 15, 19, 35)  # 25 min before the 20:00 UTC EDT close
    assert session_forced_exit("us_equity_cal", ts, close_buffer_min=10).close is False
    assert session_forced_exit("us_equity_cal", ts, close_buffer_min=30).close is True


def test_unknown_calendar_never_fires() -> None:
    """An unknown / unmapped session_calendar degrades to never-fire (safe —
    no spurious forced flat on a stream with no defined calendar close)."""
    dec = session_forced_exit("mystery_cal", _utc_ts(*_FRIDAY, 21, 55))
    assert dec.close is False


# ---------------------------------------------------------------------------
# STALE-OVERNIGHT — a restart gap around the close means the pre-close-buffer
# trigger was missed → the position survived ≥1 calendar close. Fire ALSO when
# a session close occurred between opened_ts and now AND now is IN-SESSION (so
# the venue can actually fill the flatten). TIME-only; always_on still never.
# ---------------------------------------------------------------------------


def test_equity_stale_overnight_in_session_fires() -> None:
    """A us_equity position opened BEFORE the last RTH close, now mid-RTH the
    NEXT day (in-session) → the position survived a calendar close (restart gap
    missed the pre-close flatten) → flatten now (stale-overnight)."""
    opened = _utc_ts(2026, 7, 14, 17, 0)  # 2026-07-14 13:00 ET (prior RTH)
    now = _utc_ts(2026, 7, 15, 17, 0)  # 2026-07-15 13:00 ET (next day, in RTH)
    dec = session_forced_exit("us_equity_cal", now, opened_ts=opened)
    assert dec.close is True
    assert dec.close_reason == "session_close_us_equity_cal"


def test_equity_fresh_first_session_does_not_stale_fire() -> None:
    """A position opened TODAY, still in its FIRST RTH session (no RTH close has
    elapsed since open) → does NOT stale-fire (and not near the close → the
    pre-close trigger is silent too)."""
    opened = _utc_ts(2026, 7, 15, 14, 0)  # 10:00 ET today
    now = _utc_ts(2026, 7, 15, 17, 0)  # 13:00 ET today, same session
    dec = session_forced_exit("us_equity_cal", now, opened_ts=opened)
    assert dec.close is False


def test_equity_stale_but_now_out_of_session_holds() -> None:
    """The position survived an RTH close, but NOW is overnight (out of session)
    — the venue cannot fill a flatten while closed → do NOT fire on the stale
    trigger yet (it fires at the next in-session open). Mid-RTH-fresh path keeps
    the pre-close trigger; here both are silent."""
    opened = _utc_ts(2026, 7, 14, 17, 0)  # prior-day RTH
    now = _utc_ts(2026, 7, 15, 3, 0)  # 2026-07-15 overnight (closed)
    dec = session_forced_exit("us_equity_cal", now, opened_ts=opened)
    assert dec.close is False


def test_equity_stale_overnight_pnl_irrelevant() -> None:
    """Stale-overnight is TIME-only: a deep loser AND a deep winner both flatten
    at the next in-session open (never a loss/drawdown reaction)."""
    opened = _utc_ts(2026, 7, 14, 17, 0)
    now = _utc_ts(2026, 7, 15, 17, 0)
    assert session_forced_exit(
        "us_equity_cal", now, opened_ts=opened, pnl_r=-9.0
    ).close is True
    assert session_forced_exit(
        "us_equity_cal", now, opened_ts=opened, pnl_r=9.0
    ).close is True


def test_equity_pre_close_buffer_still_fires_with_opened_ts() -> None:
    """The EXISTING pre-close-buffer trigger is preserved when opened_ts is
    threaded: opened mid-session today, now within the buffer of today's close
    → fire (existing behaviour, not the stale path)."""
    opened = _utc_ts(2026, 7, 15, 17, 0)  # 13:00 ET today
    now = _utc_ts(2026, 7, 15, 19, 55)  # 5 min before 16:00 ET close
    dec = session_forced_exit("us_equity_cal", now, opened_ts=opened)
    assert dec.close is True


def test_always_on_never_stale_fires() -> None:
    """always_on (OKX) NEVER fires even with a long-held position spanning many
    days — crypto is 24/7, there is no calendar close to have survived."""
    opened = _utc_ts(2026, 7, 1, 12, 0)
    now = _utc_ts(2026, 7, 15, 17, 0)
    dec = session_forced_exit("always_on", now, opened_ts=opened)
    assert dec.close is False


def test_fx_stale_over_weekend_in_session_fires() -> None:
    """An FX position opened before the Friday 22:00 UTC weekend close, now
    Monday mid-session (in-session, weekend close elapsed) → flatten (survived a
    calendar close; the restart gap missed the pre-close flatten)."""
    opened = _utc_ts(2026, 5, 29, 12, 0)  # Friday before the 22:00 UTC close
    now = _utc_ts(2026, 6, 1, 12, 0)  # Monday midday (FX in-session)
    dec = session_forced_exit("fx_indices_cal", now, opened_ts=opened)
    assert dec.close is True
    assert dec.close_reason == "session_close_fx_indices_cal"


def test_fx_fresh_same_session_does_not_stale_fire() -> None:
    """An FX position opened Wednesday, now Wednesday later the same week (no
    weekend close elapsed) → does NOT stale-fire."""
    opened = _utc_ts(*_WEDNESDAY, 9, 0)
    now = _utc_ts(*_WEDNESDAY, 12, 0)
    dec = session_forced_exit("fx_indices_cal", now, opened_ts=opened)
    assert dec.close is False


def test_no_opened_ts_keeps_pure_pre_close_behaviour() -> None:
    """Backward-compat: with no opened_ts the rail is the pre-close-buffer rail
    only (no stale path) — mid-RTH with no opened_ts holds."""
    dec = session_forced_exit("us_equity_cal", _utc_ts(2026, 7, 15, 17, 0))
    assert dec.close is False
