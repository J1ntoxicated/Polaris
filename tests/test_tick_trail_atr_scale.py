"""G7 tick-path trail-width ATR-scale fix ([[g7_tick_trail_atr_scale_2026-06-25]]).

DEMO/PAPER · aggressive · flow_not_block — precise EXIT TIMING only (no size cut,
no entry block, no halt; the G6 -1.0R rail is untouched).

ROOT CAUSE (live, measured): the per-tick exit pass feeds ``run_precise_exit``
the seconds-scale ``_window_atr_pct`` (mid-range over the few-second feature
window) as ``atr_pct``. ``evaluate_exit`` correctly anchors the R-unit DENOMINATOR
on ``entry_atr_pct`` (bar scale) — but the ATR-TRAIL WIDTH (``atr_one``) used the
same seconds-scale ``atr_pct``, which is ~10x smaller than the bar ATR the trail
multipliers (2.0-4.0x) were calibrated against. The trail then sits microscopically
below the peak and a single sub-tick reversal stops a fresh winner at ~flat.

Live ledger corroboration: ``atr_trail_stop`` cadence='tick' — avg hold 16.6s,
min 0.0s, avg mfe_r +0.14R, realised ~0R (winner give-back); cadence='bar' (a real
bar ATR feeds the trail) — avg hold 662s, +0.001R, healthy.

FIX: ``evaluate_exit`` accepts an optional ``trail_atr_pct`` — the STABLE bar-scale
ATR for the TRAIL WIDTH only. ``None`` (every existing caller, incl. the bar
recalc) keeps ``atr_pct`` for the trail → byte-identical (the bar Chandelier still
tracks today's noise band). The tick exit pass passes ``trail_atr_pct=entry_atr_pct``
so the trail distance is anchored to the entry-time bar risk, not the sub-second
range. The R denominator, FSM rungs, peak-fraction floor and the -1.0R rail are all
untouched.

ENTRY=100, entry_atr_pct=0.0012 (0.12%, the live flow_pressure median); the
seconds-scale tick window atr_pct=0.0001 (0.01%).
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    MfeProtectSchedule,
    evaluate_exit,
    init_exit_state,
)

ENTRY = 100.0
ENTRY_ATR_PCT = 0.0012  # bar-scale risk (atr_one = 0.12, atr_r = 0.24)
TICK_ATR_PCT = 0.0001  # seconds-scale _window_atr_pct (atr_one = 0.01)
TRAIL = 4.0  # flow_pressure wider let-winners-run trail (_momentum_trail_mult)

# flow_pressure MFE-protect schedule (with peak-fraction arm at +1.0R).
FP_SCHED = MfeProtectSchedule(
    bep_at_r=0.30, protect_at_r=0.45, lock_r=0.20,
    peak_lock_arm_r=1.0, peak_lock_frac=0.50,
)


def _fresh(side: str = "long") -> object:
    return init_exit_state(entry_price=ENTRY, side=side)


# --- the relative ATR floor (task #51) already widens the seconds-scale trail --


def test_relative_floor_widens_seconds_scale_trail_no_clip() -> None:
    """Task #51 floor (``_ATR_PCT_RELATIVE_FLOOR`` 1e-4 → 1e-3) interaction.

    The seconds-scale tick ``atr_pct`` (0.0001 = 0.01%) is BELOW the 0.1% relative
    floor, so ``_atr_one_usd`` now floors the trail-width ATR at ``entry * 1e-3 =
    0.1`` instead of the old ``entry * 1e-4 = 0.01``. The trail width is therefore
    4 * 0.1 = 0.4 and the stop sits at 100.06 - 0.4 = 99.66 (was the microscopic
    100.02 under the old 1e-4 floor). The same tiny pullback to 100.01 no longer
    touches the stop — the floor unification already neutralises the seconds-scale
    winner give-back this module's ``trail_atr_pct`` fix targets (the two
    mitigations now overlap; ``trail_atr_pct`` remains a finer refinement).
    """
    up = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.06,
        atr_pct=TICK_ATR_PCT, pnl_r=0.25, held_seconds=5,
        entry_atr_pct=ENTRY_ATR_PCT, trail_mult=TRAIL, mfe_protect=FP_SCHED,
    )
    assert up.state.stop_price == 99.66  # floored trail: peak - 4*0.1
    pullback = evaluate_exit(
        prev=up.state, side="long", entry_price=ENTRY, last_price=100.01,
        atr_pct=TICK_ATR_PCT, pnl_r=0.04, held_seconds=6,
        entry_atr_pct=ENTRY_ATR_PCT, trail_mult=TRAIL, mfe_protect=FP_SCHED,
    )
    # The floored trail no longer clips the fresh winner on the next tick.
    assert pullback.close is False
    assert pullback.close_reason is None


def test_fix_trail_atr_pct_anchors_trail_to_bar_scale() -> None:
    """FIX: trail_atr_pct=entry_atr_pct anchors the TRAIL WIDTH to the bar ATR.

    The trail width = 4 * (100 * 0.0012) = 0.48 → stop at 100.06 - 0.48 = 99.58,
    far below. The same tiny pullback to 100.01 does NOT stop the winner — it runs.
    """
    up = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.06,
        atr_pct=TICK_ATR_PCT, pnl_r=0.25, held_seconds=5,
        entry_atr_pct=ENTRY_ATR_PCT, trail_mult=TRAIL, mfe_protect=FP_SCHED,
        trail_atr_pct=ENTRY_ATR_PCT,
    )
    assert up.state.stop_price == 99.58  # bar-scale trail: peak - 0.48
    pullback = evaluate_exit(
        prev=up.state, side="long", entry_price=ENTRY, last_price=100.01,
        atr_pct=TICK_ATR_PCT, pnl_r=0.04, held_seconds=6,
        entry_atr_pct=ENTRY_ATR_PCT, trail_mult=TRAIL, mfe_protect=FP_SCHED,
        trail_atr_pct=ENTRY_ATR_PCT,
    )
    assert pullback.close is False
    assert pullback.close_reason is None


def test_fix_short_side_symmetric() -> None:
    """Short side: trail_atr_pct anchors the trail above the trough symmetrically."""
    up = evaluate_exit(
        prev=_fresh("short"), side="short", entry_price=ENTRY, last_price=99.94,
        atr_pct=TICK_ATR_PCT, pnl_r=0.25, held_seconds=5,
        entry_atr_pct=ENTRY_ATR_PCT, trail_mult=TRAIL, mfe_protect=FP_SCHED,
        trail_atr_pct=ENTRY_ATR_PCT,
    )
    assert up.state.stop_price == 100.42  # trough + 0.48
    pullback = evaluate_exit(
        prev=up.state, side="short", entry_price=ENTRY, last_price=99.99,
        atr_pct=TICK_ATR_PCT, pnl_r=0.04, held_seconds=6,
        entry_atr_pct=ENTRY_ATR_PCT, trail_mult=TRAIL, mfe_protect=FP_SCHED,
        trail_atr_pct=ENTRY_ATR_PCT,
    )
    assert pullback.close is False


def test_trail_atr_pct_none_is_byte_identical_to_atr_pct() -> None:
    """``trail_atr_pct=None`` (every existing caller, incl. the bar recalc) keeps
    the CURRENT-atr_pct Chandelier trail — byte-identical to the pre-fix engine."""
    common = dict(
        side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=0.02, pnl_r=1.0, held_seconds=30, entry_atr_pct=0.01,
        trail_mult=2.0,
    )
    legacy = evaluate_exit(prev=_fresh("long"), **common)  # type: ignore[arg-type]
    explicit = evaluate_exit(
        prev=_fresh("long"), trail_atr_pct=None, **common,  # type: ignore[arg-type]
    )
    assert legacy.state.stop_price == explicit.state.stop_price
    # And passing trail_atr_pct == atr_pct must equal the default path.
    same = evaluate_exit(
        prev=_fresh("long"), trail_atr_pct=0.02, **common,  # type: ignore[arg-type]
    )
    assert same.state.stop_price == legacy.state.stop_price


def test_bar_chandelier_unchanged_when_trail_atr_pct_omitted() -> None:
    """Guard the deliberate BAR behaviour: a wide current ATR with a narrow entry
    anchor still trails on the CURRENT ATR (the peak-fraction-floor regime). The
    fix must not touch this — trail_atr_pct is omitted on the bar path."""
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=114.66,
        atr_pct=0.04, pnl_r=7.33, held_seconds=120, entry_atr_pct=0.01,
        trail_mult=3.5,
        mfe_protect=MfeProtectSchedule(
            bep_at_r=0.35, protect_at_r=0.50, lock_r=0.25,
            peak_lock_arm_r=1.0, peak_lock_frac=0.50,
        ),
    )
    # Peak-fraction floor still lifts the stop to entry + 7.33*0.5*2.0 = 107.33
    # (unchanged from test_exit_peak_fraction_floor) — trail on the CURRENT 0.04 ATR.
    assert d.state.stop_price == ENTRY + 7.33 * 0.50 * 2.0
