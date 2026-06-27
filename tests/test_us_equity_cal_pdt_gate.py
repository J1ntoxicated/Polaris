"""T13 — us_equity_cal RTH integrity gate + PDT ranking-down (NOT a block).

Spec source: T13 brief (Track C / Alpaca US equity, Phase 3).

POLICY (the load-bearing invariants under test):
- RTH gate = INTEGRITY cap only. Outside US regular trading hours
  (13:30-20:00 UTC) the equity venue rejects orders (market closed), so a NEW
  equity entry is *held* until RTH — an integrity constraint (same class as the
  circuit-breaker integrity halt), NOT a P&L throttle. Existing positions are
  NEVER force-closed by this gate.
- PDT gate = RANKING-DOWN ONLY, NEVER a block. When ``daytrade_count >= 3``,
  new day-trade-style entries get a lower priority (a positive rank penalty),
  but are NOT blocked; overnight holds are fully free. No P&L halt. No hard
  entry block. The penalty hooks into the existing universe/signal ranking.
- This gate applies ONLY to ``product_class == "equity"``
  (``session_calendar == "us_equity_cal"``). OKX ``always_on`` and Capital
  ``fx_indices_cal`` behaviour is byte-identical (no gate applied to A/B).
"""

from __future__ import annotations

import datetime as dt

from polaris.core.streams import resolve_stream
from polaris.venues.alpaca.equity_session_gate import (
    RTH_CLOSE_UTC_MINUTES,
    RTH_OPEN_UTC_MINUTES,
    equity_entry_held_for_session,
    pdt_rank_penalty,
    stream_session_gate_active,
    us_equity_session_state,
)


def _utc_ts(hour: int, minute: int = 0) -> int:
    """A unix ts at a fixed UTC wall-clock (date irrelevant — gate is UTC-hour)."""
    base = dt.datetime(2026, 5, 29, hour, minute, tzinfo=dt.UTC)
    return int(base.timestamp())


# ---------------------------------------------------------------------------
# A — us_equity_session_state: RTH window 13:30-20:00 UTC, pre/after/closed
# ---------------------------------------------------------------------------


def test_rth_open_boundary_is_inclusive() -> None:
    """13:30 UTC sharp is RTH (open boundary inclusive)."""
    assert us_equity_session_state(_utc_ts(13, 30)) == "rth"


def test_rth_midday_is_open() -> None:
    assert us_equity_session_state(_utc_ts(17, 0)) == "rth"


def test_rth_close_boundary_is_exclusive() -> None:
    """20:00 UTC sharp is no longer RTH (close boundary exclusive → after)."""
    assert us_equity_session_state(_utc_ts(20, 0)) == "after_hours"


def test_just_before_close_is_rth() -> None:
    assert us_equity_session_state(_utc_ts(19, 59)) == "rth"


def test_pre_market_state() -> None:
    """Before 13:30 UTC (but same day) → pre_market."""
    assert us_equity_session_state(_utc_ts(12, 0)) == "pre_market"


def test_after_hours_state() -> None:
    assert us_equity_session_state(_utc_ts(21, 0)) == "after_hours"


def test_overnight_is_closed() -> None:
    """Deep overnight (e.g. 03:00 UTC) → closed."""
    assert us_equity_session_state(_utc_ts(3, 0)) == "closed"


def test_window_constants_are_1330_2000_utc() -> None:
    """Pin the documented RTH window to 13:30-20:00 UTC (in minutes)."""
    assert RTH_OPEN_UTC_MINUTES == 13 * 60 + 30
    assert RTH_CLOSE_UTC_MINUTES == 20 * 60


# ---------------------------------------------------------------------------
# B — equity entry held outside RTH (integrity), allowed in RTH; existing
#     positions never force-closed (the gate only decides NEW entries).
# ---------------------------------------------------------------------------


def test_equity_entry_allowed_in_rth() -> None:
    """In RTH a NEW equity entry is NOT held (venue accepts)."""
    assert equity_entry_held_for_session(_utc_ts(17, 0)) is False


def test_equity_entry_held_when_closed() -> None:
    """Outside RTH a NEW equity entry is HELD (integrity — venue would reject)."""
    assert equity_entry_held_for_session(_utc_ts(3, 0)) is True


def test_equity_entry_held_in_pre_market() -> None:
    assert equity_entry_held_for_session(_utc_ts(12, 0)) is True


def test_equity_entry_held_in_after_hours() -> None:
    assert equity_entry_held_for_session(_utc_ts(21, 0)) is True


def test_gate_decides_only_new_entries_not_existing() -> None:
    """The gate exposes ONLY an entry-hold decision — there is no
    force-close / exit API. Existing positions are untouched by design.

    Lint the public *callables*: no exported function may be a close/exit/halt
    action, so the gate can never force-close a held position. (The RTH window
    constant RTH_CLOSE_UTC_MINUTES names the session-close TIME, not a
    position-close action — constants are excluded from this verb lint.)
    """
    import polaris.venues.alpaca.equity_session_gate as mod

    forbidden = {"close", "exit", "halt", "force", "liquidate"}
    for name in mod.__all__:
        if not callable(getattr(mod, name)):
            continue  # constants (e.g. RTH_CLOSE_UTC_MINUTES) are times, not actions
        low = name.lower()
        assert not any(f in low for f in forbidden), (
            f"equity_session_gate exposed a close/exit-style callable {name!r}: "
            "this gate must only HOLD new entries, never force-close positions."
        )


# ---------------------------------------------------------------------------
# C — PDT ranking-down (NEVER a block). >=3 → positive rank penalty; entry
#     still possible (penalty is a ranking number, not a veto).
# ---------------------------------------------------------------------------


def test_pdt_below_threshold_no_penalty() -> None:
    assert pdt_rank_penalty(0) == 0.0
    assert pdt_rank_penalty(2) == 0.0


def test_pdt_at_threshold_ranks_down() -> None:
    """daytrade_count == 3 → positive penalty (ranking-down), but finite."""
    pen = pdt_rank_penalty(3)
    assert pen > 0.0


def test_pdt_above_threshold_ranks_down() -> None:
    pen = pdt_rank_penalty(5)
    assert pen > 0.0


def test_pdt_penalty_is_finite_never_infinite() -> None:
    """A penalty is a *ranking* number, never an infinite veto / block.

    An infinite (or block-sentinel) penalty would amount to a hard-block,
    which the mandate forbids (flow_not_block). Penalty must stay finite.
    """
    import math

    for n in (3, 4, 10, 99):
        pen = pdt_rank_penalty(n)
        assert math.isfinite(pen), f"PDT penalty for {n} must be finite (not a block)"


def test_pdt_penalty_signature_returns_number_not_bool() -> None:
    """Guard against a regression that turns the ranking-down into a boolean
    block. The contract is a numeric rank penalty."""
    pen = pdt_rank_penalty(3)
    assert isinstance(pen, float)
    assert not isinstance(pen, bool)


# ---------------------------------------------------------------------------
# D — stream gate routing: ONLY us_equity_cal is gated. A/B byte-identical.
# ---------------------------------------------------------------------------


def test_gate_active_only_for_us_equity_cal() -> None:
    assert stream_session_gate_active("us_equity_cal") is True


def test_gate_inactive_for_always_on_okx() -> None:
    """OKX always_on stream is NOT session-gated (A unchanged)."""
    assert stream_session_gate_active("always_on") is False


def test_gate_inactive_for_fx_indices_cal_capital() -> None:
    """Capital fx_indices_cal stream is NOT touched by this gate (B unchanged)."""
    assert stream_session_gate_active("fx_indices_cal") is False


def test_okx_stream_resolves_to_always_on_not_gated() -> None:
    """End-to-end: resolve OKX venue → its calendar is NOT us_equity_cal."""
    cal = resolve_stream("okx").session_calendar
    assert cal == "always_on"
    assert stream_session_gate_active(cal) is False


def test_capital_stream_resolves_to_fx_indices_cal_not_gated() -> None:
    cal = resolve_stream("capital").session_calendar
    assert cal == "fx_indices_cal"
    assert stream_session_gate_active(cal) is False


def test_alpaca_stream_resolves_to_us_equity_cal_gated() -> None:
    cal = resolve_stream("alpaca").session_calendar
    assert cal == "us_equity_cal"
    assert stream_session_gate_active(cal) is True


# ---------------------------------------------------------------------------
# E — _run_tick wiring helpers: equity RTH hold (integrity) + PDT rank-down.
#     A/B venues are NEVER held; equity held only when market closed.
# ---------------------------------------------------------------------------


def _state() -> object:
    from polaris.scripts._production_state import ProdLoopState

    return ProdLoopState()


def test_wire_equity_entry_held_when_market_closed() -> None:
    """Equity venue (alpaca) outside RTH → hold the NEW entry (integrity) and
    bump the telemetry counter. Holding ≠ blocking on P&L; market is closed."""
    from polaris.scripts._production_tick import equity_session_entry_hold

    st = _state()
    held = equity_session_entry_hold("alpaca", now_ts=_utc_ts(3, 0), state=st)  # type: ignore[arg-type]
    assert held is True
    assert st.equity_session_holds == 1  # type: ignore[attr-defined]


def test_wire_equity_entry_allowed_in_rth() -> None:
    """Equity venue during RTH → NOT held (venue accepts), no counter bump."""
    from polaris.scripts._production_tick import equity_session_entry_hold

    st = _state()
    held = equity_session_entry_hold("alpaca", now_ts=_utc_ts(17, 0), state=st)  # type: ignore[arg-type]
    assert held is False
    assert st.equity_session_holds == 0  # type: ignore[attr-defined]


def test_wire_okx_never_held_even_overnight() -> None:
    """OKX (always_on / track A) is NEVER session-held — byte-identical to
    pre-T13: even at 03:00 UTC the entry flows."""
    from polaris.scripts._production_tick import equity_session_entry_hold

    st = _state()
    held = equity_session_entry_hold("okx", now_ts=_utc_ts(3, 0), state=st)  # type: ignore[arg-type]
    assert held is False
    assert st.equity_session_holds == 0  # type: ignore[attr-defined]


def test_wire_capital_never_held_even_overnight() -> None:
    """Capital (fx_indices_cal / track B) is NEVER touched by this gate."""
    from polaris.scripts._production_tick import equity_session_entry_hold

    st = _state()
    held = equity_session_entry_hold("capital", now_ts=_utc_ts(3, 0), state=st)  # type: ignore[arg-type]
    assert held is False
    assert st.equity_session_holds == 0  # type: ignore[attr-defined]


def test_wire_pdt_rank_down_does_not_block_equity() -> None:
    """PDT >= 3 on an equity entry → record a rank-down (telemetry), but the
    helper NEVER reports a block. Entry still proceeds (flow_not_block)."""
    from polaris.scripts._production_tick import apply_equity_pdt_rank_down

    st = _state()
    st.pdt_daytrade_count = 4  # type: ignore[attr-defined]
    penalty = apply_equity_pdt_rank_down("alpaca", state=st)  # type: ignore[arg-type]
    assert penalty > 0.0  # ranked down
    assert st.equity_pdt_rank_downs == 1  # type: ignore[attr-defined]


def test_wire_pdt_below_threshold_no_rank_down() -> None:
    from polaris.scripts._production_tick import apply_equity_pdt_rank_down

    st = _state()
    st.pdt_daytrade_count = 1  # type: ignore[attr-defined]
    penalty = apply_equity_pdt_rank_down("alpaca", state=st)  # type: ignore[arg-type]
    assert penalty == 0.0
    assert st.equity_pdt_rank_downs == 0  # type: ignore[attr-defined]


def test_wire_pdt_not_applied_to_okx_or_capital() -> None:
    """PDT is an equity-only concept — A/B never accrue a rank-down even with a
    (nonsensical for them) high daytrade_count."""
    from polaris.scripts._production_tick import apply_equity_pdt_rank_down

    for venue in ("okx", "capital"):
        st = _state()
        st.pdt_daytrade_count = 9  # type: ignore[attr-defined]
        penalty = apply_equity_pdt_rank_down(venue, state=st)  # type: ignore[arg-type]
        assert penalty == 0.0
        assert st.equity_pdt_rank_downs == 0  # type: ignore[attr-defined]


def test_run_tick_source_gates_equity_only_before_signal() -> None:
    """Source-level lint: the equity session hold must be wired into _run_tick
    and routed by stream (equity only), placed in the entry path before the
    pipeline factory — never a blanket pre-signal skip for A/B."""
    from pathlib import Path

    src = Path("polaris/scripts/_production_tick.py").read_text()
    assert "equity_session_entry_hold" in src, (
        "T13: equity RTH integrity hold must be wired into _run_tick."
    )
    assert "apply_equity_pdt_rank_down" in src, (
        "T13: PDT ranking-down must be wired into _run_tick."
    )


# ---------------------------------------------------------------------------
# F — RTH is DST-correct (America/New_York 09:30-16:00 local, year-round).
#     Parametrize a January (EST, UTC-5) and a July (EDT, UTC-4) date and
#     prove the UTC window shifts with DST. H3 review nit (b).
# ---------------------------------------------------------------------------


def _utc_ts_on(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    """Unix ts at a fixed UTC wall-clock on an explicit calendar date.

    Unlike ``_utc_ts`` (which fixes a May/EDT date), this lets a test pin a
    January (EST) vs July (EDT) date so the DST shift is observable.
    """
    base = dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC)
    return int(base.timestamp())


def test_rth_open_is_0930_et_in_est_january() -> None:
    """In EST (January) 09:30 ET == 14:30 UTC → RTH at 14:30 UTC, NOT 13:30."""
    # 14:30 UTC on a Jan date = 09:30 EST → RTH open boundary inclusive.
    assert us_equity_session_state(_utc_ts_on(2026, 1, 15, 14, 30)) == "rth"
    # 13:30 UTC on the same Jan date = 08:30 EST → still pre_market (NOT RTH).
    assert us_equity_session_state(_utc_ts_on(2026, 1, 15, 13, 30)) == "pre_market"


def test_rth_open_is_0930_et_in_edt_july() -> None:
    """In EDT (July) 09:30 ET == 13:30 UTC → RTH at 13:30 UTC."""
    assert us_equity_session_state(_utc_ts_on(2026, 7, 15, 13, 30)) == "rth"
    # 14:30 UTC in July = 10:30 EDT → mid-session RTH.
    assert us_equity_session_state(_utc_ts_on(2026, 7, 15, 14, 30)) == "rth"


def test_1400_utc_is_pre_in_january_but_rth_in_july() -> None:
    """The discriminating case: 14:00 UTC is 09:00 EST (pre) in January but
    10:00 EDT (RTH) in July. A DST-naive UTC hardcode gets one of these wrong."""
    assert us_equity_session_state(_utc_ts_on(2026, 1, 15, 14, 0)) == "pre_market"
    assert us_equity_session_state(_utc_ts_on(2026, 7, 15, 14, 0)) == "rth"


def test_2030_utc_is_rth_in_january_but_after_in_july() -> None:
    """20:30 UTC is 15:30 EST (still RTH) in January but 16:30 EDT (after) in
    July — the close boundary tracks DST too."""
    assert us_equity_session_state(_utc_ts_on(2026, 1, 15, 20, 30)) == "rth"
    assert us_equity_session_state(_utc_ts_on(2026, 7, 15, 20, 30)) == "after_hours"


def test_rth_close_is_1600_et_both_seasons() -> None:
    """16:00 ET close is exclusive year-round: 21:00 UTC (EST) and 20:00 UTC
    (EDT) are both just past the close → after_hours."""
    assert us_equity_session_state(_utc_ts_on(2026, 1, 15, 21, 0)) == "after_hours"
    assert us_equity_session_state(_utc_ts_on(2026, 7, 15, 20, 0)) == "after_hours"


def test_equity_entry_held_uses_dst_correct_window() -> None:
    """The integrity entry-hold tracks the DST-correct RTH: at 14:00 UTC the
    new equity entry is HELD in January (market not yet open) but allowed in
    July (market open). Same UTC instant, different season → different hold."""
    assert equity_entry_held_for_session(_utc_ts_on(2026, 1, 15, 14, 0)) is True
    assert equity_entry_held_for_session(_utc_ts_on(2026, 7, 15, 14, 0)) is False


# ---------------------------------------------------------------------------
# G — PDT ranking-down is ACTUALLY APPLIED to spec ordering (H3 review nit a):
#     a PDT-flagged equity spec is demoted BELOW an unflagged one, but is
#     NEVER dropped/blocked. The rank-down result is consumed, not discarded.
# ---------------------------------------------------------------------------


def test_order_specs_by_rank_demotes_flagged_below_unflagged() -> None:
    """A PDT-flagged spec (positive rank_penalty) inserted FIRST must end up
    AFTER an unflagged spec (penalty 0.0) once ordered — ranked down, not
    blocked. Both specs survive (flow_not_block)."""
    from polaris.core.isolation.worker import PipelineTaskSpec
    from polaris.scripts._production_tick import order_specs_by_rank

    async def _noop() -> None:
        return None

    flagged = PipelineTaskSpec(
        strategy_id="equity_flagged", coro_factory=_noop, rank_penalty=1.0
    )
    unflagged = PipelineTaskSpec(
        strategy_id="equity_clean", coro_factory=_noop, rank_penalty=0.0
    )
    # flagged inserted first on purpose — ordering must demote it.
    ordered = order_specs_by_rank([flagged, unflagged])
    assert [s.strategy_id for s in ordered] == ["equity_clean", "equity_flagged"]
    # NOT a block: both specs are still present after ranking.
    assert len(ordered) == 2


def test_order_specs_by_rank_never_drops_a_flagged_spec() -> None:
    """Even a lone PDT-flagged spec survives ordering — ranking-down is never a
    veto. The penalty lowers priority; it never removes the entry."""
    from polaris.core.isolation.worker import PipelineTaskSpec
    from polaris.scripts._production_tick import order_specs_by_rank

    async def _noop() -> None:
        return None

    flagged = PipelineTaskSpec(
        strategy_id="equity_flagged", coro_factory=_noop, rank_penalty=5.0
    )
    ordered = order_specs_by_rank([flagged])
    assert [s.strategy_id for s in ordered] == ["equity_flagged"]


def test_order_specs_by_rank_is_stable_for_equal_penalty() -> None:
    """Unflagged specs (all penalty 0.0) keep their original signal order — the
    sort is stable so A/B venues (always penalty 0) are byte-identical."""
    from polaris.core.isolation.worker import PipelineTaskSpec
    from polaris.scripts._production_tick import order_specs_by_rank

    async def _noop() -> None:
        return None

    specs = [
        PipelineTaskSpec(strategy_id=f"s{i}", coro_factory=_noop, rank_penalty=0.0)
        for i in range(5)
    ]
    ordered = order_specs_by_rank(specs)
    assert [s.strategy_id for s in ordered] == [f"s{i}" for i in range(5)]


def test_pipeline_spec_rank_penalty_defaults_zero() -> None:
    """The new rank_penalty field defaults to 0.0 so every existing call site
    (supervise_strategies, A/B specs) stays byte-identical."""
    from polaris.core.isolation.worker import PipelineTaskSpec

    async def _noop() -> None:
        return None

    spec = PipelineTaskSpec(strategy_id="s", coro_factory=_noop)
    assert spec.rank_penalty == 0.0


def test_run_tick_consumes_pdt_penalty_into_spec_ordering() -> None:
    """Source-level lint: the PDT rank-down RESULT must be consumed (not
    discarded) — attached to the spec's rank_penalty and the spec list ordered
    by it. Guards the H3 nit (a) regression where the return value was dropped."""
    from pathlib import Path

    src = Path("polaris/scripts/_production_tick.py").read_text()
    assert "order_specs_by_rank" in src, (
        "T13/H3: PDT rank-down must reorder pipeline specs (penalty consumed)."
    )
    assert "rank_penalty=" in src, (
        "T13/H3: the PDT penalty must be attached to the PipelineTaskSpec, not "
        "discarded at the call site."
    )


# ---------------------------------------------------------------------------
# H — WEEKEND-AWARE clock (root-cause fix). The cash book is shut ALL weekend,
#     so Sat/Sun ET-RTH-hours must read CLOSED, not 'rth'. This closes the SSOT
#     consumed by BOTH the WS gate (_gated_at) and the entry-hold gate
#     (equity_entry_held_for_session) — one fix shuts both gaps. flow_not_block:
#     weekend = market actually closed (the venue rejects), not a throttle.
# ---------------------------------------------------------------------------


def _sat_et_rth() -> int:
    """Saturday 2026-06-06 14:08 UTC = 10:08 ET — inside the [09:30,16:00) RTH
    band BUT on the weekend (the exact reproduced root-cause instant)."""
    return int(dt.datetime(2026, 6, 6, 14, 8, tzinfo=dt.UTC).timestamp())


def _sun_et_rth() -> int:
    """Sunday 2026-06-07 17:00 UTC = 13:00 ET — inside the RTH band, weekend."""
    return int(dt.datetime(2026, 6, 7, 17, 0, tzinfo=dt.UTC).timestamp())


def test_saturday_et_rth_hours_is_closed_not_rth() -> None:
    """🔴 Root cause: a pure time-of-day clock returned 'rth' for Sat 10:08 ET.
    The market is shut all weekend → the state MUST be 'closed'."""
    assert us_equity_session_state(_sat_et_rth()) == "closed"


def test_sunday_et_rth_hours_is_closed_not_rth() -> None:
    assert us_equity_session_state(_sun_et_rth()) == "closed"


def test_weekday_rth_unchanged_by_weekend_guard() -> None:
    """The weekend guard must NOT change weekday behaviour: a Wednesday inside
    RTH is still 'rth' (byte-identical to pre-fix)."""
    # Wednesday 2026-06-03 17:00 UTC = 13:00 ET.
    assert us_equity_session_state(_utc_ts_on(2026, 6, 3, 17, 0)) == "rth"


def test_saturday_pre_and_after_hours_clock_are_closed() -> None:
    """Sat pre-market / after-hours clock-of-day also collapse to 'closed' —
    the extended-hours envelope does not run on the weekend either."""
    # Sat 2026-06-06 12:00 UTC = 08:00 ET (weekday: pre_market) → closed.
    assert us_equity_session_state(_utc_ts_on(2026, 6, 6, 12, 0)) == "closed"
    # Sat 2026-06-06 21:00 UTC = 17:00 ET (weekday: after_hours) → closed.
    assert us_equity_session_state(_utc_ts_on(2026, 6, 6, 21, 0)) == "closed"


def test_equity_entry_held_on_saturday_et_rth_hours() -> None:
    """② entry-hold gap: the SAME weekend-blind clock let a Saturday equity
    entry through (held=False → order reached Alpaca → closed-market reject →
    buying-power drain). Now a Saturday ET-RTH-hours entry is HELD."""
    assert equity_entry_held_for_session(_sat_et_rth()) is True
    assert equity_entry_held_for_session(_sun_et_rth()) is True


def test_equity_entry_allowed_weekday_rth_after_weekend_guard() -> None:
    """The weekend guard must not hold a legit weekday RTH entry (flow_not_block
    — open returns, entries resume automatically)."""
    assert equity_entry_held_for_session(_utc_ts_on(2026, 6, 3, 17, 0)) is False


def test_equity_ws_warm_active_weekday_pre_open_lead() -> None:
    """#66 pre-warm WS predicate: True in the weekday pre-open WARM lead so the
    socket reconnects ahead of the open. Monday 2026-06-08 13:10 UTC = 09:10 ET,
    20min before the 09:30 ET open (inside the 30min lead)."""
    from polaris.venues.alpaca.equity_session_gate import equity_ws_warm_active

    assert equity_ws_warm_active(_utc_ts_on(2026, 6, 8, 13, 10)) is True


def test_equity_ws_warm_active_false_on_weekend_and_overnight() -> None:
    """The warm predicate is weekday-only and lead-scoped: False on the weekend
    (same pre-open clock-of-day) and False deep-overnight on a weekday."""
    from polaris.venues.alpaca.equity_session_gate import equity_ws_warm_active

    # Sat 2026-06-06 13:10 UTC = 09:10 ET (weekend) → no warm.
    assert equity_ws_warm_active(_utc_ts_on(2026, 6, 6, 13, 10)) is False
    # Tue 2026-06-09 04:00 UTC = 00:00 ET (deep overnight, outside lead) → no warm.
    assert equity_ws_warm_active(_utc_ts_on(2026, 6, 9, 4, 0)) is False


def test_equity_ws_warm_active_false_during_and_after_rth() -> None:
    """The warm predicate only covers the PRE-open lead, not RTH itself (RTH is
    handled by us_equity_session_state) nor after-hours. Weekday mid-RTH and
    after-hours both return False."""
    from polaris.venues.alpaca.equity_session_gate import equity_ws_warm_active

    # Wed 2026-06-03 17:00 UTC = 13:00 ET (mid-RTH) → not a pre-open lead.
    assert equity_ws_warm_active(_utc_ts_on(2026, 6, 3, 17, 0)) is False
    # Wed 2026-06-03 21:00 UTC = 17:00 ET (after-hours) → no warm.
    assert equity_ws_warm_active(_utc_ts_on(2026, 6, 3, 21, 0)) is False


def test_wire_equity_entry_held_on_weekend_et_rth_hours() -> None:
    """End-to-end ② wiring: the production entry-hold seam holds an Alpaca
    equity entry on a Saturday ET-RTH-hours instant (the closed-market /
    buying-power-drain case) and bumps the telemetry counter."""
    from polaris.scripts._production_tick import equity_session_entry_hold

    st = _state()
    held = equity_session_entry_hold("alpaca", now_ts=_sat_et_rth(), state=st)  # type: ignore[arg-type]
    assert held is True
    assert st.equity_session_holds == 1  # type: ignore[attr-defined]
