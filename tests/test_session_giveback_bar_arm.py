"""session_breakout exit give-back fix — bar-TREND peak-lock arm lowered 0.5→0.30.

DEMO/PAPER · aggressive · flow_not_block: EXIT-TIMING ONLY — size / entry side /
the G6 -1.0R hard rail are untouched. This only ratchets the protective stop
toward profit on a confirmed favourable excursion.

DIAGNOSIS (archive ``polaris_live_archive_2026-06-26`` + ``_2026-06-25``,
session_breakout, n=98 closed; the n=1 garbage-close outlier pos_bfc4 with a
1070.63 close_px on US100~29780 excluded):

  * ENTRY HAS EDGE (category a, NOT a no-edge strategy): avg peak MFE +0.4034R,
    48.0% reach +0.30R, 28.6% reach +0.50R — the entry IS directional.
  * REAL exit give-back: in a CONSISTENT dollar ruler (peak/trough price
    excursion vs realised close $), the armed positions (fav > $0.5, n=14) give
    back avg 74.4% of the favourable dollar move (pos_e2f2 reached +$16.2,
    realised +$7.74 = 48% capture; pos_c316 52% capture; pos_37cb 49%).
  * ROOT CAUSE of the give-back: the bar-TREND family (session_breakout /
    equity_gap_go / fx_breakout_basket) armed its peak-fraction floor at
    ``BAR_TREND_PEAK_LOCK_ARM_R`` = 0.50R — ABOVE the strategy's 0.40R avg MFE.
    The #19/#47 fixes lowered only the TICK-path arm (``_TICK_PEAK_LOCK_ARM_R``
    0.45→0.30); the SEPARATE bar-TREND constant was left at 0.50, so the
    common-case 0.30–0.45R bar winner never armed the floor and round-tripped on
    the wide 3.0-ATR trail to break-even.

  NOTE the AUTOPSY's headline "99.98% giveback" was OVERSTATED by a ruler
  mismatch (#51 residue): ``mfe_r`` divides by the per-trade ATR risk (~$24.7)
  while ``pnl_r`` divides by the per-stream R_budget ($1,020) — a 41× skew, so
  ``(mfe_r − pnl_r)/mfe_r`` is apples-to-oranges. The TRUE dollar give-back is
  ~74% (armed), still a real harvest defect this fix targets.

FIX: ``BAR_TREND_PEAK_LOCK_ARM_R`` 0.50 → 0.30 so the bar-TREND peak-fraction
floor arms on the COMMON case (48% reach +0.30R), matching the already-lowered
tick arm. ASYMMETRY preserved: the floor RATCHETS UP with the peak (a big runner
— pos_bfc4 +13.8R, pos_e2f2 +1.85R — still runs on the 3.0-ATR trail), and the
loss side (peak == entry → no profit floor; the G6 -1.0R rail owns it) is
untouched.

ENTRY=100, ATR_PCT=0.01 -> atr_one=1.0, atr_r (module default)=2.0 -> 1R==2.0 px.
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    ExitState,
    evaluate_exit,
    init_exit_state,
)
from polaris.scripts.exit_strategy_config import (
    BAR_TREND_PEAK_LOCK_ARM_R,
    BAR_TREND_PEAK_LOCK_FRAC,
    BAR_TREND_TRAIL_MULT,
    _mfe_protect_for_strategy,
)

ENTRY = 100.0
ATR_PCT = 0.01  # atr_one = 1.0, atr_r = 2.0 (module default)


def _fresh(side: str = "long") -> ExitState:
    return init_exit_state(entry_price=ENTRY, side=side)


# === the constant + the wired schedule ==========================================


def test_bar_trend_arm_lowered_to_common_case() -> None:
    # The bar-TREND peak-fraction arm now matches the tick arm (#47): the
    # common-case 0.40R-avg bar winner arms the floor. 48.0% of session_breakout
    # closes reach +0.30R vs only 28.6% reaching the old +0.50R arm.
    assert BAR_TREND_PEAK_LOCK_ARM_R == 0.30
    assert BAR_TREND_PEAK_LOCK_FRAC == 0.55  # UNCHANGED
    assert BAR_TREND_TRAIL_MULT == 3.0  # UNCHANGED — the runner still runs wide


def test_session_breakout_schedule_arms_at_030() -> None:
    # session_breakout (correlation_group cfd_session_event → TREND bucket, index
    # asset_class) routes to the bar default schedule with the lowered arm.
    sched = _mfe_protect_for_strategy("session_breakout")
    assert sched is not None
    assert sched.peak_lock_arm_r == 0.30
    assert sched.peak_lock_frac == 0.55


# === the give-back is caught at the strategy's AVERAGE peak ======================


def test_avg_peak_locks_fraction_not_break_even() -> None:
    # A session_breakout winner peaks at +0.40R (the MEASURED avg). With the arm
    # at 0.30R the peak-fraction floor ARMS and locks
    # entry + 0.40*0.55*atr_r = 100 + 0.44 = 100.44 (+0.22R) — instead of the old
    # arm-0.50 behaviour that left it at break-even (the give-back).
    sched = _mfe_protect_for_strategy("session_breakout")
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.8,
        atr_pct=ATR_PCT, pnl_r=0.40, held_seconds=20,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d.state.mfe_r == (100.8 - ENTRY) / 2.0  # +0.40R reached
    assert d.close is False
    # peak-fraction floor: 100 + mfe_r * 0.55 * atr_r (the running peak, +0.22R).
    assert d.state.stop_price == ENTRY + d.state.mfe_r * 0.55 * 2.0


def test_avg_peak_give_back_closes_at_locked_fraction() -> None:
    # Two ticks (a short — session_breakout is 52% short in the archive): peak
    # +0.40R locks 99.56; a retrace to 99.7 (worse than the floor for a short)
    # closes AT the locked +0.22R — NOT a flat 100.0 round-trip.
    sched = _mfe_protect_for_strategy("session_breakout")
    d1 = evaluate_exit(
        prev=_fresh("short"), side="short", entry_price=ENTRY, last_price=99.2,
        atr_pct=ATR_PCT, pnl_r=0.40, held_seconds=20,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d1.close is False
    floor = ENTRY - 0.40 * 0.55 * 2.0  # 99.56
    assert d1.state.stop_price == floor
    d2 = evaluate_exit(
        prev=d1.state, side="short", entry_price=ENTRY, last_price=99.7,
        atr_pct=ATR_PCT, pnl_r=0.15, held_seconds=30,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d2.close is True
    assert d2.close_reason == "atr_trail_stop"
    assert d2.state.stop_price == floor  # banked the fraction, not flat BEP


# === ASYMMETRY: the runner still runs, the loss side is untouched ===============


def test_runner_still_runs_floor_ratchets_up() -> None:
    # flow_not_block / aggressive: the floor RATCHETS UP with the peak — it never
    # caps the upside. A runner +0.40R -> +1.85R (pos_e2f2) climbs its floor
    # 100.44 -> 100 + 1.85*0.55*2.0; it does NOT close while above the floor.
    sched = _mfe_protect_for_strategy("session_breakout")
    d1 = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.8,
        atr_pct=ATR_PCT, pnl_r=0.40, held_seconds=20,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    d2 = evaluate_exit(
        prev=d1.state, side="long", entry_price=ENTRY, last_price=103.7,
        atr_pct=ATR_PCT, pnl_r=1.85, held_seconds=40,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d2.close is False  # the winner keeps running
    assert d2.state.stop_price == ENTRY + 1.85 * 0.55 * 2.0  # floor climbed up


def test_loss_side_untouched_no_profit_floor() -> None:
    # ASYMMETRY: a position that never went green (peak == entry) gets NO profit
    # floor — the wide trail sits below entry and the G6 -1.0R rail (in the
    # orchestrator) owns the loss side, unchanged by this arm lowering.
    sched = _mfe_protect_for_strategy("session_breakout")
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=99.0,
        atr_pct=ATR_PCT, pnl_r=-0.5, held_seconds=20,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d.close is False
    assert d.state.stop_price is not None and d.state.stop_price < ENTRY


def test_below_new_arm_is_break_even_only() -> None:
    # A peak just BELOW the new 0.30 arm (0.25R) still does NOT arm the
    # peak-fraction floor — only the fixed BEP rung (0.30R) governs, so a
    # sub-0.30R wiggle is never over-protected (no fee-negative crumb lock).
    sched = _mfe_protect_for_strategy("session_breakout")
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.5,
        atr_pct=ATR_PCT, pnl_r=0.25, held_seconds=20,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    # 0.25R < 0.30 arm AND < 0.30 BEP rung → no floor; wide trail only (< entry).
    assert d.state.stop_price is not None and d.state.stop_price < ENTRY
