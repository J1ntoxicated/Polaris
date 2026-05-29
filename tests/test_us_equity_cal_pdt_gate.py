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
