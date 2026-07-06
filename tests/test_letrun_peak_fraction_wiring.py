"""let-winners-run peak-fraction WIRING ([[ab_letrun_maker_2026-06-24]]).

Proves the bar-recalc + tick-engine resolvers arm the peak-fraction floor for the
right families and leave the REVERSION / range bucket UNCHANGED:
  * bar TREND family (session_breakout / equity_gap_go / fx_breakout_basket) →
    peak-fraction arm +0.20R / frac 0.65 + a widened 3.0-ATR trail (arm lowered
    0.30→0.20 to catch the common +0.20R winner, frac raised 0.55→0.65 to shrink
    big-winner give-back, [[session_giveback_bar_arm]]);
  * bar REVERSION / range bucket → arm 0.0 (disabled) + None trail (byte-identical);
  * tick momentum (burst_rider / flow_pressure) → peak-fraction arm +1.0R / frac
    0.50 + a wide trail (burst 3.5, flow_pressure 4.0), low sub-arm rungs preserved.

DEMO/PAPER · aggressive · flow_not_block: every arm only RATCHETS the stop toward
profit — size / entry side / the G6 -1.0R rail are untouched.
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import Bucket
from polaris.core.ticks.config import TickEngineConfig
from polaris.scripts._production_recalc_exit import (
    BAR_TREND_PEAK_LOCK_ARM_R,
    BAR_TREND_PEAK_LOCK_FRAC,
    BAR_TREND_TRAIL_MULT,
    _bucket_for_strategy,
    _mfe_protect_for_strategy,
    _trail_mult_for_strategy,
)
from polaris.scripts._production_tick_mfe import (
    _BURST_RIDER_MFE_RUNGS,
    _TICK_PEAK_LOCK_ARM_R,
    _TICK_PEAK_LOCK_FRAC,
    _mfe_protect_schedule,
    _momentum_trail_mult,
)

# The design-named bar TREND family. (session_breakout un-registered
# 2026-07-06 — B1 prune, live-ledger forensic, -$933.65 fee-bleed — swapped
# for xau_indices_trend, the remaining live Capital TREND-bucket bar
# strategy.)
_BAR_TREND = ("xau_indices_trend", "fx_breakout_basket")
# REVERSION-bucket bar strategies (their edge is a bounded revert-to-mean).
# (fx_range_fade was un-registered in the strategy-wave1 restructure → its
# registry-backed bucket now defaults to the let-run TREND default, so it is no
# longer a live REVERSION bar strategy; rsi_bb_pullback is the remaining one.)
_BAR_REVERSION = ("rsi_bb_pullback",)


# --- bar TREND family arms the peak-fraction floor + wide trail -----------------


def test_bar_trend_family_arms_peak_fraction_and_wide_trail() -> None:
    for sid in _BAR_TREND:
        assert _bucket_for_strategy(sid) is Bucket.TREND
        sched = _mfe_protect_for_strategy(sid)
        assert sched is not None
        assert sched.peak_lock_arm_r == BAR_TREND_PEAK_LOCK_ARM_R == 0.20
        assert sched.peak_lock_frac == BAR_TREND_PEAK_LOCK_FRAC == 0.65
        assert _trail_mult_for_strategy(sid) == BAR_TREND_TRAIL_MULT == 3.0


# --- bar REVERSION / range bucket is UNCHANGED (disabled arm, default trail) ----


def test_bar_reversion_bucket_unchanged() -> None:
    for sid in _BAR_REVERSION:
        assert _bucket_for_strategy(sid) is Bucket.REVERSION
        sched = _mfe_protect_for_strategy(sid)
        assert sched is not None
        # The fixed sub-arm rungs still exist (the harvest floor), but the
        # peak-fraction let-winners-run floor is DISABLED for reversion.
        assert sched.peak_lock_arm_r == 0.0
        assert sched.peak_lock_frac == 0.0
        assert _trail_mult_for_strategy(sid) is None  # module-default trail


# --- tick momentum arms +0.30R / frac 0.50, keeps low sub-arm rungs (#47) -------


def test_tick_burst_rider_peak_fraction_keeps_sub_arm_rungs() -> None:
    cfg = TickEngineConfig()
    sched = _mfe_protect_schedule("burst_rider", cfg, regime=None)
    assert sched is not None
    # Low sub-arm rungs (0.35/0.50/0.25) PRESERVED as the below-arm phase.
    bep, protect, lock = _BURST_RIDER_MFE_RUNGS
    assert sched.bep_at_r == bep
    assert sched.protect_at_r == protect
    assert sched.lock_r == lock
    # Peak-fraction arm +0.30R (#47 earlier common-case rung) / frac 0.50 on a
    # 3.5-ATR wide trail.
    assert sched.peak_lock_arm_r == _TICK_PEAK_LOCK_ARM_R == 0.30
    assert sched.peak_lock_frac == _TICK_PEAK_LOCK_FRAC == 0.50
    assert _momentum_trail_mult("burst_rider") == 3.5


def test_tick_flow_pressure_peak_fraction_armed() -> None:
    cfg = TickEngineConfig()
    sched = _mfe_protect_schedule("flow_pressure", cfg, regime=None)
    assert sched is not None
    # flow_pressure keeps its cfg-SSOT sub-arm rungs.
    assert sched.bep_at_r == cfg.mfe_bep_r
    assert sched.protect_at_r == cfg.mfe_protect_r
    assert sched.lock_r == cfg.mfe_protect_lock_r
    # Same peak-fraction arm (+0.30R, #47); the wide flow_pressure trail (4.0)
    # is unchanged.
    assert sched.peak_lock_arm_r == _TICK_PEAK_LOCK_ARM_R == 0.30
    assert sched.peak_lock_frac == _TICK_PEAK_LOCK_FRAC == 0.50
    assert _momentum_trail_mult("flow_pressure") == 4.0


def test_tick_micro_reversion_unmapped_none() -> None:
    cfg = TickEngineConfig()
    # micro_reversion is reversion family (scalp exit) → no run_precise_exit
    # schedule and no momentum trail (byte-identical).
    assert _mfe_protect_schedule("micro_reversion", cfg, regime=None) is None
    assert _momentum_trail_mult("micro_reversion") is None
