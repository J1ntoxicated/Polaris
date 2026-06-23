"""let-winners-run — peak-fraction floor unit tests ([[ab_letrun_maker_2026-06-24]]).

DEMO/PAPER · aggressive · flow_not_block. burst_rider peaked +7.33R but realised
+0.137R — the fixed lock (+0.25R) + 60% give-back force-close + 1-ATR HARVEST
trail flat-lined the winner. The peak-fraction floor RATCHETS the stop to a
FRACTION of the reached peak MFE once an arm rung is passed:
``floor = entry ± peak_mfe_r * peak_lock_frac * atr_r`` (R-units), composed with
the fixed-rung floor via ``max()`` (phase: fixed dominates below the crossover,
peak-fraction above) and monotone vs the prior stop. EXPECTANCY, not a throttle:
size / entry side / the G6 -1.0R rail are untouched; it ONLY ratchets the stop
toward profit. ``peak_lock_arm_r == 0.0`` (default) disables it → byte-identical.

ENTRY=100, ATR_PCT=0.01 → atr_one=1.0, atr_r (2x)=2.0 → 1R == 2.0 in price.
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    EXIT_STATE_PROTECTED,
    ExitState,
    MfeProtectSchedule,
    evaluate_exit,
    init_exit_state,
)

ENTRY = 100.0
ATR_PCT = 0.01  # atr_one = 1.0, atr_r = 2.0

# burst_rider peak-fraction: low sub-arm rungs (0.35/0.50/0.25) PLUS the
# peak-fraction arm at +1.0R, frac 0.50, on a WIDE 3.5-ATR running trail.
BURST_SCHED = MfeProtectSchedule(
    bep_at_r=0.35, protect_at_r=0.50, lock_r=0.25,
    peak_lock_arm_r=1.0, peak_lock_frac=0.50,
)
WIDE_TRAIL = 3.5


def _fresh(side: str = "long") -> ExitState:
    return init_exit_state(entry_price=ENTRY, side=side)


# --- The headline bug: a +7R burst on a WIDE-ATR run is held at ~peak%, not flat.
# The running trail anchors on the CURRENT atr_pct; the peak-fraction floor uses
# the ENTRY-anchored atr_r. When ATR is wide (4x) THROUGHOUT the run-up, the wide
# trail sits far below the peak — the entry-anchored floor catches the winner at
# peak%. entry_atr_pct=0.01 pins atr_r=2.0 for the floor; run-up atr_pct=0.04 →
# atr_one=4.0 for the trail (114.66 - 3.5*4.0 = 100.66, a +7R→+0.33R round-trip).


def test_peak_fraction_holds_big_burst_under_wide_atr_long() -> None:
    # Single tick, wide ATR the whole time: peak 114.66 → mfe 7.33R (entry-anchored
    # denominator). The bare wide trail = 100.66; the peak-fraction floor lifts the
    # stop to entry + 7.33*0.5*2.0 = 107.33 (== +3.665R locked, not a +0.33R flat).
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=114.66,
        atr_pct=0.04, pnl_r=7.33, held_seconds=120,
        entry_atr_pct=ATR_PCT, trail_mult=WIDE_TRAIL, mfe_protect=BURST_SCHED,
    )
    assert d.state.mfe_r >= 7.0
    assert d.state.stop_price == ENTRY + 7.33 * 0.50 * 2.0  # 107.33, not 100.66


def test_peak_fraction_holds_big_burst_under_wide_atr_short() -> None:
    d = evaluate_exit(
        prev=_fresh("short"), side="short", entry_price=ENTRY, last_price=85.34,
        atr_pct=0.04, pnl_r=7.33, held_seconds=120,
        entry_atr_pct=ATR_PCT, trail_mult=WIDE_TRAIL, mfe_protect=BURST_SCHED,
    )
    assert d.state.mfe_r >= 7.0
    assert d.state.stop_price == ENTRY - 7.33 * 0.50 * 2.0  # 92.67, not the balloon


# --- Phase composition: BELOW the arm rung the fixed floor still governs --------


def test_below_arm_keeps_fixed_floor_not_peak_fraction() -> None:
    # peak 101.0 → mfe 0.50R, BELOW the +1.0R peak-arm. The fixed protect rung
    # (+0.50R → lock +0.25R floor 100.5) governs; the peak-fraction branch (which
    # at 0.5R*0.5 would only lock 100.5 too) must NOT raise it differently — and
    # critically the sub-arm low rungs are preserved (flow_not_block: the low-rung
    # phase keeps protecting before the big-winner arm engages).
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=101.0,
        atr_pct=ATR_PCT, pnl_r=0.50, held_seconds=20,
        trail_mult=WIDE_TRAIL, mfe_protect=BURST_SCHED,
    )
    assert d.state.mfe_r >= 0.50
    assert d.state.stop_price == ENTRY + 0.25 * 2.0  # 100.5, the fixed lock


def test_sub_arm_bep_rung_preserved() -> None:
    # peak 100.7 → mfe 0.35R: the sub-arm BEP rung floors at entry; the wide trail
    # (96.65) sits far below. The peak-arm (+1.0R) is not reached → no clobber.
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.7,
        atr_pct=ATR_PCT, pnl_r=0.35, held_seconds=10,
        trail_mult=WIDE_TRAIL, mfe_protect=BURST_SCHED,
    )
    assert d.state.stop_price == ENTRY  # BEP, not the bare wide trail


# --- The crossover: peak-fraction OVERTAKES the fixed lock above the arm --------


def test_peak_fraction_overtakes_fixed_lock_above_arm() -> None:
    # peak 104.0 → mfe 2.0R (≥ +1.0R arm). peak-fraction floor = 100 + 2.0*0.5*2.0
    # = 102.0 (== +1.0R). The fixed +0.25R lock (100.5) is dominated → peak wins.
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=60,
        trail_mult=WIDE_TRAIL, mfe_protect=BURST_SCHED,
    )
    assert d.state.stop_price == ENTRY + 2.0 * 0.50 * 2.0  # 102.0


# --- give-back: a +7R burst on wide ATR that retraces closes at the lock ---------


def test_burst_retrace_closes_at_peak_fraction_lock_not_flat() -> None:
    # Tick 1: peak 114.66 (+7.33R) on wide ATR (the trail never tightened past the
    # floor) → peak-fraction floor binds the stop at 107.33.
    d1 = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=114.66,
        atr_pct=0.04, pnl_r=7.33, held_seconds=120,
        entry_atr_pct=ATR_PCT, trail_mult=WIDE_TRAIL, mfe_protect=BURST_SCHED,
    )
    assert d1.close is False
    assert d1.state.stop_price == ENTRY + 7.33 * 0.50 * 2.0  # 107.33
    # Tick 2: price retraces to 106.0 (< 107.33) → trail-stop close AT the locked
    # +3.665R, NOT a flat breakeven round-trip (the original burst_rider bug).
    d2 = evaluate_exit(
        prev=d1.state, side="long", entry_price=ENTRY, last_price=106.0,
        atr_pct=0.04, pnl_r=3.0, held_seconds=130,
        entry_atr_pct=ATR_PCT, trail_mult=WIDE_TRAIL, mfe_protect=BURST_SCHED,
    )
    assert d2.close is True
    assert d2.close_reason == "atr_trail_stop"
    assert d2.state.stop_price == ENTRY + 7.33 * 0.50 * 2.0  # locked, not flat


# --- monotone: the peak-fraction floor never loosens a tighter prior stop -------


def test_peak_fraction_floor_never_loosens_prior_tighter_stop() -> None:
    prev = ExitState(
        peak_price=104.0, trough_price=ENTRY, stop_price=103.0,
        exit_state=EXIT_STATE_PROTECTED,
    )
    # mfe 2.0R → peak floor 102.0, BELOW the prior tighter 103.0 → ratchet holds.
    d = evaluate_exit(
        prev=prev, side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=60,
        trail_mult=WIDE_TRAIL, mfe_protect=BURST_SCHED,
    )
    assert d.state.stop_price == 103.0


# --- disabled (arm_r == 0.0) is byte-identical to the fixed-only schedule -------


def test_peak_lock_disabled_is_fixed_only() -> None:
    fixed_only = MfeProtectSchedule(bep_at_r=0.35, protect_at_r=0.50, lock_r=0.25)
    # peak 104.0 → mfe 2.0R → HARVEST state. With peak-lock DISABLED (arm_r 0.0)
    # the HARVEST trail collapses to the original 1-ATR EXIT_HARVEST_TRAIL_MULT
    # (104 - 1.0 = 103.0); the fixed +0.25R lock (100.5) is below it → 103.0 wins.
    # This proves the disabled path is byte-identical to the pre-feature behaviour
    # (no wide let-run-harvest trail, no peak-fraction lift).
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=60,
        trail_mult=WIDE_TRAIL, mfe_protect=fixed_only,
    )
    assert d.state.exit_state == "harvest"
    assert d.state.stop_price == 103.0  # 1-ATR harvest collapse, the original behaviour


# --- HARVEST mode with a peak-armed schedule keeps the WIDE trail (no double-cut)


def test_harvest_mode_holds_wide_trail_when_peak_armed() -> None:
    # A soft give-back (gb==1) → HARVEST mode. With a peak-armed schedule the trail
    # must NOT collapse to the 1-ATR harvest mult (the flat-line bug) — the
    # let-run-harvest wide trail is held and the peak-fraction floor supplies the
    # lock. Peak 104.0 (+2.0R ≥ +1.0R arm) on wide ATR; soft give-back: pnl 1.2R
    # (surrendered (2-1.2)/2 = 0.40 > frac 0.50? no). Use a give-back that arms soft.
    from polaris.core.live_recalc.exit_engine import (
        ManagementMode,
        ThesisGivebackParams,
    )

    gb = ThesisGivebackParams(arm_r=0.30, frac=0.30, hard_frac=0.75)
    # peak 104.0 (+2.0R), pnl 1.0R → surrendered (2-1)/2 = 0.50 > frac 0.30 (soft),
    # ≤ hard 0.75 → gb==1 → HARVEST mode, NOT a fast-close. On wide ATR (0.04) the
    # bare trail would be 104-3.5*4=90.0; the let-run-harvest trail (3.5*atr_one)
    # is held and the +2.0R peak-fraction floor (102.0) protects.
    d = evaluate_exit(
        prev=ExitState(
            peak_price=104.0, trough_price=ENTRY, stop_price=None,
            exit_state=EXIT_STATE_PROTECTED,
        ),
        side="long", entry_price=ENTRY, last_price=103.5,
        atr_pct=0.04, pnl_r=1.0, held_seconds=60, entry_atr_pct=ATR_PCT,
        trail_mult=WIDE_TRAIL, mfe_protect=BURST_SCHED,
        mode=ManagementMode.HARVEST, thesis_giveback=gb,
    )
    # Not fast-closed (soft give-back), and the peak-fraction floor (102.0) holds —
    # NOT the 1-ATR harvest collapse (103.5 - 1*4 = 99.5) nor a flat breakeven.
    assert d.close is False
    assert d.state.stop_price == ENTRY + 2.0 * 0.50 * 2.0  # 102.0, the peak floor


# --- wire round-trip: an old dict without peak_lock_* decodes to disabled --------


def test_mfe_protect_dict_round_trip_and_legacy_default() -> None:
    from polaris.core.live_recalc.exit_engine import (
        mfe_protect_from_dict,
        mfe_protect_to_dict,
    )

    rt = mfe_protect_from_dict(mfe_protect_to_dict(BURST_SCHED))
    assert rt == BURST_SCHED  # full round-trip preserves the peak-fraction pair
    # A legacy dict that predates the peak-fraction floor (no peak_lock_* keys)
    # decodes to the DISABLED default (0.0) → fixed-only, byte-identical.
    legacy = mfe_protect_from_dict(
        {"bep_at_r": 0.35, "protect_at_r": 0.50, "lock_r": 0.25}
    )
    assert legacy is not None
    assert legacy.peak_lock_arm_r == 0.0
    assert legacy.peak_lock_frac == 0.0
