"""bar-TREND exit give-back recalib — peak-lock arm 0.30→0.20 / frac 0.55→0.65.

DEMO/PAPER · aggressive · flow_not_block: EXIT-TIMING ONLY — size / entry side /
the G6 -1.0R hard rail are untouched. This only ratchets the protective stop
toward profit on a confirmed favourable excursion (asymmetry: the floor RATCHETS
UP with the peak, the loss side is owned by the G6 rail).

DIAGNOSIS (live ``data/polaris_live.sqlite``, position_strategy_segments
exit_reason join — bar-TREND family: session_breakout / fx_breakout_basket /
xau_indices_trend / spot_donchian):

  * ENTRY HAS EDGE (positive MFE): atr_trail_stop closes (n=20) are the biggest
    leak — big winners round-trip on the wide trail. The peak-fraction floor IS
    wired and DOES fire (volume_burst locked_R = peak×0.55 confirmed live), but
    two weaknesses remained:
      (A) frac 0.55 always EXPOSES 45% of the reached peak on a big winner.
      (B) arm 0.30R caught the COMMON small winner too late: the live MFE
          distribution reaches +0.20R for 27.9%, +0.30R for 20%, +0.45R for
          10.6% — so the frequent +0.20R winner armed nothing and gave it all
          back to loser_timeout / shallow exit.

FIX (surgical, two env-tunable defaults only):
  * ``BAR_TREND_PEAK_LOCK_ARM_R`` 0.30 → 0.20 — the common +0.20R winner (27.9%
    reach it) now ARMS the peak-fraction floor instead of round-tripping.
  * ``BAR_TREND_PEAK_LOCK_FRAC`` 0.55 → 0.65 — a big winner's give-back shrinks
    from 45% to 35% of the reached peak; the floor is a peak-tracking ratchet, so
    a higher frac is NOT an early cut (it climbs with the peak, never caps it).
  * ``BAR_TREND_TRAIL_MULT`` 3.0 UNCHANGED — narrowing it would clip the frequent
    winner early (a loss); the lock is supplied by the peak-fraction floor, not
    the trail.

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
    # The bar-TREND peak-fraction arm now catches the COMMON +0.20R winner
    # (live MFE distribution: 27.9% reach +0.20R vs only 20% reaching +0.30R).
    assert BAR_TREND_PEAK_LOCK_ARM_R == 0.20
    # frac raised 0.55 → 0.65: a big winner now keeps 65% of the reached peak
    # (give-back 45% → 35%); still a peak-tracking ratchet, never an early cut.
    assert BAR_TREND_PEAK_LOCK_FRAC == 0.65
    assert BAR_TREND_TRAIL_MULT == 3.0  # UNCHANGED — the runner still runs wide


def test_bar_trend_schedule_arms_at_020() -> None:
    # xau_indices_trend (correlation_group cfd_index_commodity_trend → TREND
    # bucket, index/commodity asset_class) routes to the bar default schedule
    # with the lowered arm / raised fraction. (session_breakout, the original
    # exemplar, was un-registered 2026-07-06 — B1 prune, live-ledger
    # forensic, -$933.65 fee-bleed — so this test now exercises the
    # remaining live Capital TREND-bucket bar strategy; the wiring under test
    # is bucket-keyed, not strategy-specific.)
    sched = _mfe_protect_for_strategy("xau_indices_trend")
    assert sched is not None
    assert sched.peak_lock_arm_r == 0.20
    assert sched.peak_lock_frac == 0.65


# === (a) the COMMON +0.20R winner now arms a profit floor (was unarmed) =========


def test_common_020_winner_arms_profit_floor() -> None:
    # A bar-TREND long peaks at +0.20R (27.9% of live closes reach it).
    # With arm 0.20 the peak-fraction floor ARMS and locks a profit stop ABOVE
    # entry: 100 + 0.20*0.65*atr_r = 100 + 0.26 = 100.26 (stop > entry).
    # Under the OLD 0.30 arm this +0.20R winner armed NOTHING (0.20 < 0.30 arm AND
    # < 0.30 BEP rung) → wide trail only → stop <= entry → round-trip.
    sched = _mfe_protect_for_strategy("xau_indices_trend")
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.4,
        atr_pct=ATR_PCT, pnl_r=0.20, held_seconds=20,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d.state.mfe_r == (100.4 - ENTRY) / 2.0  # +0.20R reached
    assert d.close is False
    assert d.state.stop_price is not None
    # peak-fraction floor armed: 100 + 0.20 * 0.65 * 2.0 = 100.26 (profit-side).
    assert d.state.stop_price == ENTRY + d.state.mfe_r * BAR_TREND_PEAK_LOCK_FRAC * 2.0
    assert d.state.stop_price > ENTRY  # the give-back fix: a PROFIT stop, not BEP


# === (b) frac 0.65 holds a big winner higher than the old 0.55 ==================


def test_big_winner_floor_higher_under_065_frac() -> None:
    # A big winner (+2.0R MFE) locks entry + 2.0*0.65*atr_r = 100 + 2.6 = 102.6,
    # strictly ABOVE the old 0.55-frac floor (100 + 2.0*0.55*2.0 = 102.2). The
    # give-back exposure shrank from 45% to 35% of the reached peak.
    sched = _mfe_protect_for_strategy("xau_indices_trend")
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=40,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d.state.mfe_r == 2.0
    new_floor = ENTRY + 2.0 * 0.65 * 2.0  # 102.6
    old_floor = ENTRY + 2.0 * 0.55 * 2.0  # 102.2
    assert d.state.stop_price == new_floor
    assert new_floor > old_floor  # the give-back-shrink direction


# === (c) -1.0R rail invariance: profit-side ratchet ONLY ========================


def test_loss_side_untouched_no_profit_floor() -> None:
    # ASYMMETRY / -1.0R rail invariant: a position that never went green
    # (peak == entry) gets NO profit floor — the wide trail sits BELOW entry and
    # the G6 -1.0R rail (in the orchestrator) owns the loss side, unchanged by the
    # arm/frac recalib. The peak-fraction floor never pushes the stop below entry.
    sched = _mfe_protect_for_strategy("xau_indices_trend")
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=99.0,
        atr_pct=ATR_PCT, pnl_r=-0.5, held_seconds=20,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d.close is False
    assert d.state.stop_price is not None and d.state.stop_price < ENTRY


def test_below_new_arm_no_premature_profit_floor() -> None:
    # A peak just BELOW the new 0.20 arm (0.15R) still does NOT arm the
    # peak-fraction floor (and is below the 0.30 BEP rung) → no profit floor; the
    # wide trail (< entry) governs. No fee-negative crumb lock below the arm.
    sched = _mfe_protect_for_strategy("xau_indices_trend")
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.3,
        atr_pct=ATR_PCT, pnl_r=0.15, held_seconds=20,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d.state.mfe_r == (100.3 - ENTRY) / 2.0  # +0.15R, below the 0.20 arm
    assert d.state.mfe_r < BAR_TREND_PEAK_LOCK_ARM_R  # under the arm → no floor
    assert d.state.stop_price is not None and d.state.stop_price < ENTRY


# === (d) REVERSION / range bucket UNCHANGED — arm 0.0, peak_lock disabled =======


def test_reversion_bucket_arm_zero_unchanged() -> None:
    # A REVERSION-bucket bar strategy leaves the peak-fraction floor DISABLED
    # (arm 0.0 → byte-identical) — its edge is a bounded revert-to-mean, never a
    # let-winners-run. The arm/frac recalib touches the TREND family ONLY.
    sched = _mfe_protect_for_strategy("rsi_bb_pullback")
    assert sched is not None
    assert sched.peak_lock_arm_r == 0.0
    assert sched.peak_lock_frac == 0.0


# === (e) let-run: no premature close while the peak keeps making new highs ======


def test_runner_still_runs_floor_ratchets_up() -> None:
    # flow_not_block / aggressive: the floor RATCHETS UP with the peak — never caps
    # the upside, never closes while price is above the floor. A runner armed at
    # +0.20R climbs +0.20R -> +0.40R -> +1.85R; each tick makes a new high (price
    # well above the climbing floor) so it does NOT close (let-winners-run).
    sched = _mfe_protect_for_strategy("xau_indices_trend")
    d1 = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.4,
        atr_pct=ATR_PCT, pnl_r=0.20, held_seconds=20,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d1.close is False
    assert d1.state.stop_price == ENTRY + 0.20 * 0.65 * 2.0  # 100.26
    d2 = evaluate_exit(
        prev=d1.state, side="long", entry_price=ENTRY, last_price=100.8,
        atr_pct=ATR_PCT, pnl_r=0.40, held_seconds=30,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d2.close is False
    assert d2.state.stop_price == ENTRY + 0.40 * 0.65 * 2.0  # 100.52 — climbed up
    d3 = evaluate_exit(
        prev=d2.state, side="long", entry_price=ENTRY, last_price=103.7,
        atr_pct=ATR_PCT, pnl_r=1.85, held_seconds=40,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d3.close is False  # the winner keeps running
    assert d3.state.stop_price == ENTRY + 1.85 * 0.65 * 2.0  # floor climbed again


# === (f) peak-protect fires: a give-back closes AT the locked fraction ===========


def test_peak_give_back_closes_at_locked_fraction() -> None:
    # Two ticks (a short): peak +0.40R locks the 0.65-fraction floor at
    # 100 - 0.40*0.65*2.0 = 99.48; a retrace to 99.6 (worse than the floor for a
    # short) closes AT the locked +0.26R via atr_trail_stop — banking the fraction,
    # NOT a flat 100.0 round-trip.
    sched = _mfe_protect_for_strategy("xau_indices_trend")
    d1 = evaluate_exit(
        prev=_fresh("short"), side="short", entry_price=ENTRY, last_price=99.2,
        atr_pct=ATR_PCT, pnl_r=0.40, held_seconds=20,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d1.close is False
    floor = ENTRY - 0.40 * 0.65 * 2.0  # 99.48
    assert d1.state.stop_price == floor
    d2 = evaluate_exit(
        prev=d1.state, side="short", entry_price=ENTRY, last_price=99.6,
        atr_pct=ATR_PCT, pnl_r=0.20, held_seconds=30,
        trail_mult=BAR_TREND_TRAIL_MULT, mfe_protect=sched,
    )
    assert d2.close is True
    assert d2.close_reason == "atr_trail_stop"
    assert d2.state.stop_price == floor  # banked the locked fraction, not flat BEP
