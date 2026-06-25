"""#19 peak give-back fix — small-peak protection + reversion scalp give-back.

DEMO/PAPER · aggressive · flow_not_block (peak protection, NOT entry block) ·
asymmetry preserved (the loss side — G6 -1.0R rail + the reversion ``scalp_stop``
-0.4R micro-stop — is UNTOUCHED; only the PROFIT side gains a give-back catch).

MEASURED (76 closed trades, post-reset 2026-06-25):
  * avg peak mfe_r +0.241, max +1.849; only 7.9% ever reach +1.0R, but 18.4% reach
    +0.45R and 32.9% reach +0.30R. The peak-fraction floor armed at +1.0R → it
    starved the common case (avg peak +0.39R), so a small peak round-tripped on the
    wide ATR trail with no fraction locked.
  * micro_reversion is 63% of closes (48/76) and is REVERSION family → it exits via
    ``_scalp_exit_decision`` only (scalp_target 0.35R / scalp_stop -0.4R /
    flow_reversal); it had NO peak protection between entry and the 0.35R target, so
    a +0.30R peak that reverses gave it ALL back to scalp_stop / flow_reversal.

FIX:
  1. Lower the peak-fraction floor arm 1.0 → 0.45 (the fee-safe rung — frac 0.50 →
     locks >= +0.225R at the arm — and 2.3x the coverage of +1.0R). Momentum cohort.
  2. Add a peak-fraction GIVE-BACK to the reversion scalp: once a reversion peaked
     past ``_SCALP_PEAK_ARM_R`` and surrenders below ``peak * _SCALP_PEAK_FRAC``
     (still POSITIVE), bank ``scalp_giveback`` instead of round-tripping to
     scalp_stop / flow_reversal. PROFIT side only — never touches scalp_stop.

ENTRY=100, ATR_PCT=0.01 -> atr_one=1.0, atr_r (2x)=2.0 -> 1R == 2.0 in price.
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    EXIT_PEAK_LOCK_ARM_R,
    EXIT_PEAK_LOCK_FRAC,
    ExitState,
    MfeProtectSchedule,
    evaluate_exit,
    init_exit_state,
)
from polaris.scripts._production_tick_mfe import (
    _SCALP_PEAK_ARM_R,
    _SCALP_PEAK_FRAC,
    _SCALP_PEAK_MIN_BANK_R,
    _SCALP_STOP_R,
    _TICK_PEAK_LOCK_ARM_R,
    _scalp_exit_decision,
)

ENTRY = 100.0
ATR_PCT = 0.01  # atr_one = 1.0, atr_r = 2.0


def _fresh(side: str = "long") -> ExitState:
    return init_exit_state(entry_price=ENTRY, side=side)


def _sched() -> MfeProtectSchedule:
    return MfeProtectSchedule(
        bep_at_r=0.30, protect_at_r=0.45, lock_r=0.20,
        peak_lock_arm_r=EXIT_PEAK_LOCK_ARM_R, peak_lock_frac=EXIT_PEAK_LOCK_FRAC,
    )


# === EDIT 1 — momentum peak-fraction arm lowered to the common-case rung =======


def test_peak_arm_lowered_to_fee_safe_common_rung() -> None:
    # The arm must drop from the dead +1.0R (only 7.9% reach) to the fee-safe
    # +0.45R rung (18.4% reach) so the avg +0.39R peak is actually protected.
    # frac stays 0.50 -> at the arm the lock is +0.225R (clears fees).
    assert EXIT_PEAK_LOCK_ARM_R == 0.45
    assert _TICK_PEAK_LOCK_ARM_R == 0.45
    assert EXIT_PEAK_LOCK_FRAC == 0.50


def test_small_peak_locks_fraction_of_peak_long() -> None:
    # A momentum winner peaks +0.50R (mass: 18-30% of trades reach here) then this
    # tick still at peak. With the arm at 0.45R the peak-fraction floor ARMS and
    # locks entry + 0.50*0.50*atr_r = 100 + 0.50 = 100.5 (== +0.25R), instead of
    # leaving the small peak on the wide trail (96.5) with nothing locked.
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=101.0,
        atr_pct=ATR_PCT, pnl_r=0.50, held_seconds=20,
        trail_mult=3.5, mfe_protect=_sched(),
    )
    assert d.state.mfe_r >= 0.50
    # peak-fraction floor (100 + 0.50*0.50*2.0 = 100.5) dominates the fixed
    # lock (protect rung 0.45 -> +0.20R -> 100.4) -> 100.5 locked.
    assert d.state.stop_price == ENTRY + 0.50 * 0.50 * 2.0  # 100.5


def test_small_peak_give_back_closes_at_locked_fraction_not_flat() -> None:
    # Two ticks: peak +0.50R locks 100.5; a retrace to 100.3 (< 100.5) closes at the
    # locked +0.25R -- NOT a flat 100.0 round-trip (the #19 give-back bug).
    d1 = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=101.0,
        atr_pct=ATR_PCT, pnl_r=0.50, held_seconds=20,
        trail_mult=3.5, mfe_protect=_sched(),
    )
    assert d1.close is False
    assert d1.state.stop_price == 100.5
    d2 = evaluate_exit(
        prev=d1.state, side="long", entry_price=ENTRY, last_price=100.3,
        atr_pct=ATR_PCT, pnl_r=0.15, held_seconds=30,
        trail_mult=3.5, mfe_protect=_sched(),
    )
    assert d2.close is True
    assert d2.close_reason == "atr_trail_stop"
    assert d2.state.stop_price == 100.5  # locked fraction, not flat break-even


def test_peak_fraction_still_runs_the_winner_not_a_cap() -> None:
    # flow_not_block / aggressive: the floor RATCHETS UP with the peak -- it never
    # caps the upside. A winner running +0.50R -> +2.0R sees its floor climb
    # 100.5 -> 102.0; it does not close while price stays above the floor.
    d1 = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=101.0,
        atr_pct=ATR_PCT, pnl_r=0.50, held_seconds=20,
        trail_mult=3.5, mfe_protect=_sched(),
    )
    d2 = evaluate_exit(
        prev=d1.state, side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=40,
        trail_mult=3.5, mfe_protect=_sched(),
    )
    assert d2.close is False  # the winner keeps running
    assert d2.state.stop_price == ENTRY + 2.0 * 0.50 * 2.0  # 102.0, floor climbed up


def test_loss_rail_untouched_below_entry() -> None:
    # ASYMMETRY: a long that never went green (peak == entry) gets NO profit floor --
    # no protect floor is armed at/above entry, so the wide trail sits below entry
    # and the G6 -1.0R rail in the orchestrator owns the loss side, unchanged.
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=99.0,
        atr_pct=ATR_PCT, pnl_r=-0.5, held_seconds=20,
        trail_mult=3.5, mfe_protect=_sched(),
    )
    assert d.close is False
    assert d.state.stop_price is not None and d.state.stop_price < ENTRY


# === EDIT 2 — reversion scalp peak give-back (the 63% micro_reversion cohort) ===


def test_reversion_peak_give_back_banks_positive_fraction() -> None:
    # A micro_reversion long peaked +0.30R (peak_r) then this tick is at +0.15R --
    # a give-back below peak*frac. It must bank ``scalp_giveback`` (a POSITIVE
    # close) instead of holding for the 0.35R target it will now never reach (it
    # round-trips to scalp_stop / flow_reversal otherwise). 100.15 -> entry+0.15R.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.15, ofi=0.3, pnl_r=0.15,
            strategy_id="micro_reversion", peak_r=0.30,
        )
        == "scalp_giveback"
    )
    # Symmetric short.
    assert (
        _scalp_exit_decision(
            side="short", entry_price=100.0, last_mid=99.85, ofi=-0.3, pnl_r=0.15,
            strategy_id="micro_reversion", peak_r=0.30,
        )
        == "scalp_giveback"
    )


def test_reversion_peak_give_back_holds_near_peak() -> None:
    # Still near the peak (pnl_r above peak*frac) -> HOLD (do not bank early; let it
    # reach for the target). peak 0.30, frac 0.60 -> threshold 0.18; pnl 0.25 > 0.18.
    assert _SCALP_PEAK_FRAC == 0.60
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.25, ofi=0.3, pnl_r=0.25,
            strategy_id="micro_reversion", peak_r=0.30,
        )
        is None
    )


def test_reversion_peak_give_back_needs_armed_peak() -> None:
    # A peak BELOW the arm (0.10R < arm) never arms the give-back -- a tiny wiggle
    # must not bank crumbs. pnl gives back but peak never armed -> HOLD.
    assert _SCALP_PEAK_ARM_R == 0.25
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.02, ofi=0.3, pnl_r=0.02,
            strategy_id="micro_reversion", peak_r=0.10,
        )
        is None
    )


def test_reversion_give_back_never_fires_on_loss_side() -> None:
    # ASYMMETRY: the give-back is PROFIT-side only. A reversion that armed a peak
    # then went NEGATIVE must exit via the UNCHANGED scalp_stop (-0.4R), never a
    # ``scalp_giveback`` (which only banks a positive). At -0.5R -> scalp_stop.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=99.0, ofi=0.3,
            pnl_r=_SCALP_STOP_R - 0.1, strategy_id="micro_reversion", peak_r=0.30,
        )
        == "scalp_stop"
    )
    # A small negative pnl (above the stop) with an armed peak -> NOT scalp_giveback
    # (positive-only) and not yet scalp_stop -> hold, never a give-back.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=99.9, ofi=0.3, pnl_r=-0.05,
            strategy_id="micro_reversion", peak_r=0.30,
        )
        is None
    )


def test_reversion_give_back_floors_at_fee_safe_minimum() -> None:
    # FEE SAFETY (adversarial review): the give-back must NOT bank a fee-negative
    # crumb. A peak that just armed at 0.25R then snaps to +0.02R surrenders below
    # peak*frac (0.15) but +0.02R is below the fee-safe floor -> HOLD (it rides to
    # scalp_stop / flow_reversal instead of banking a crumb).
    assert _SCALP_PEAK_MIN_BANK_R == 0.10
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.02, ofi=0.3, pnl_r=0.02,
            strategy_id="micro_reversion", peak_r=0.25,
        )
        is None
    )
    # A give-back that survives ABOVE the fee-safe floor still banks. peak 0.30,
    # frac 0.60 -> threshold 0.18; pnl 0.12 is < 0.18 AND >= 0.10 floor -> bank.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.12, ofi=0.3, pnl_r=0.12,
            strategy_id="micro_reversion", peak_r=0.30,
        )
        == "scalp_giveback"
    )


def test_reversion_scalp_target_still_priority() -> None:
    # The 0.35R target still banks first when reached (peak give-back never pre-empts
    # a target hit) -- and a peak give-back never blocks the target path.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.4, ofi=0.3, pnl_r=0.40,
            strategy_id="micro_reversion", peak_r=0.40,
        )
        == "scalp_target"
    )


def test_no_peak_arg_is_byte_identical_legacy() -> None:
    # Omitting ``peak_r`` (legacy callers / momentum) -> the give-back branch is
    # never armed -> byte-identical to the pre-fix scalp decision.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=100.15, ofi=0.3, pnl_r=0.15,
            strategy_id="micro_reversion",
        )
        is None
    )
