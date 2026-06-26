"""#47 exit/harvest re-calibration (debate-converged + Jin sign-off).

DEMO/PAPER · aggressive · flow_not_block: every change here is EXIT-TIMING ONLY —
size / entry side / the -1.0R hard rail are untouched. Three calibrations:

  ① ``_TICK_PEAK_LOCK_ARM_R`` 0.45 -> 0.30 — the tick MOMENTUM peak-fraction floor
     arms EARLIER (32.9% of the common case reaches +0.30R; the +0.45R arm starved
     it). ``_TICK_PEAK_LOCK_FRAC`` 0.50 UNCHANGED.
  ② peak >= 1.0R BINARY disarm of the give-back HARVEST force-close — once a
     position's reached peak MFE clears +1.0R, the HARD give-back fast-close
     (``thesis_harvest``) is DISARMED so the rare runner rides on the peak-fraction
     floor + the wide ATR trail (ASYMMETRY: small peak banks, rare runner runs).
     peak < 1.0R keeps the existing give-back harvest.
  ③ ``_SCALP_PEAK_FRAC`` is now an env-knob READ FRESH per call
     (``POLARIS_SCALP_PEAK_FRAC``, default 0.60, no import-time cache) so 0.75 can
     be injected for the live A/B without a restart. ``_SCALP_PEAK_ARM_R`` 0.25
     UNCHANGED.

Non-regression: the -1.0R / scalp -0.4R loss rails are never loosened; the
give-back is PROFIT-side only; no new <=1 sizing multiplier (exit-timing only).
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    EXIT_PEAK_GIVEBACK_DISARM_R,
    ExitState,
    MfeProtectSchedule,
    evaluate_exit,
)
from polaris.core.live_recalc.exit_thesis import mode_to_exit_params
from polaris.core.live_recalc.exit_types import (
    Bucket,
    ManagementMode,
    ThesisGivebackParams,
)
from polaris.core.ticks.config import TickEngineConfig
from polaris.scripts._production_tick_mfe import (
    _SCALP_PEAK_ARM_R,
    _SCALP_STOP_R,
    _mfe_protect_schedule,
    _scalp_exit_decision,
    _scalp_peak_frac,
)

# --- ① tick peak-lock arms earlier (0.45 -> 0.30) -------------------------------


def test_spec1_tick_peak_lock_arm_is_030() -> None:
    from polaris.scripts._production_tick_mfe import (
        _TICK_PEAK_LOCK_ARM_R,
        _TICK_PEAK_LOCK_FRAC,
    )

    assert _TICK_PEAK_LOCK_ARM_R == 0.30  # was 0.45 — arms on the common case
    assert _TICK_PEAK_LOCK_FRAC == 0.50  # UNCHANGED


def test_spec1_momentum_schedule_arms_at_030() -> None:
    cfg = TickEngineConfig()
    for sid in ("burst_rider", "flow_pressure"):
        sched = _mfe_protect_schedule(sid, cfg, regime=None)
        assert sched is not None
        assert sched.peak_lock_arm_r == 0.30
        assert sched.peak_lock_frac == 0.50


# --- ② peak >= 1.0R binary disarm of the give-back harvest -----------------------


def _giveback_params() -> ThesisGivebackParams:
    # arm 0.30, soft 0.50, hard 0.75 (the module defaults).
    return ThesisGivebackParams(arm_r=0.30, frac=0.50, hard_frac=0.75)


def _peak_armed_schedule() -> MfeProtectSchedule:
    # A momentum schedule carrying the peak-fraction arm (so the floor + wide trail
    # are present — exactly what spec ② keeps when the give-back is disarmed).
    return MfeProtectSchedule(
        bep_at_r=0.20, protect_at_r=0.30, lock_r=0.15,
        peak_lock_arm_r=0.30, peak_lock_frac=0.50,
    )


def test_spec2_disarm_constant_default_is_1R() -> None:
    assert EXIT_PEAK_GIVEBACK_DISARM_R == 1.0


def test_spec2_hard_giveback_below_1R_still_harvests() -> None:
    # peak +0.80R (< 1.0R), surrendered ~88% (pnl 0.10 of peak 0.80) > hard 0.75 ->
    # the give-back harvest STAYS armed for the common small peak.
    tp = mode_to_exit_params(
        ManagementMode.HARVEST, bucket=Bucket.TREND,
        mfe_r=0.80, pnl_r=0.10, giveback=_giveback_params(),
        base_mfe_protect=_peak_armed_schedule(), base_profit_target_r=None,
    )
    assert tp.thesis_harvest is True


def test_spec2_hard_giveback_at_or_above_1R_is_disarmed() -> None:
    # peak +1.50R (>= 1.0R), surrendered ~93% (pnl 0.10 of peak 1.50) > hard 0.75 ->
    # the give-back harvest is DISARMED (the rare runner runs); the floor + wide
    # let-run-harvest trail REMAIN (floor+ATR trail만).
    tp = mode_to_exit_params(
        ManagementMode.HARVEST, bucket=Bucket.TREND,
        mfe_r=1.50, pnl_r=0.10, giveback=_giveback_params(),
        base_mfe_protect=_peak_armed_schedule(), base_profit_target_r=None,
    )
    assert tp.thesis_harvest is False  # disarmed
    # floor + ATR trail survive: the schedule is still threaded and the trail is the
    # WIDE let-run-harvest mult (not collapsed to the 1-ATR tighten).
    assert tp.mfe_protect is not None
    assert tp.mfe_protect.peak_lock_arm_r == 0.30
    assert tp.trail_mult is not None and tp.trail_mult >= 3.0


def test_spec2_disarm_boundary_exactly_1R() -> None:
    # The disarm is peak >= 1.0R (the runner threshold itself disarms).
    tp = mode_to_exit_params(
        ManagementMode.HARVEST, bucket=Bucket.TREND,
        mfe_r=1.00, pnl_r=0.05, giveback=_giveback_params(),
        base_mfe_protect=_peak_armed_schedule(), base_profit_target_r=None,
    )
    assert tp.thesis_harvest is False


def test_spec2_runner_not_force_closed_by_evaluate_exit() -> None:
    # End-to-end: a +1.0R-peak winner that gives some back must NOT be force-closed
    # by a give-back harvest while still green — it rides the floor/trail. (mode is
    # supplied so the thesis ladder is live.)
    prev = ExitState(
        peak_price=110.0, trough_price=100.0, stop_price=None,
        exit_state="harvest", mfe_r=1.20, mae_r=0.0,
    )
    dec = evaluate_exit(
        prev=prev, side="long", entry_price=100.0, last_price=105.0,
        atr_pct=0.05, pnl_r=0.50, held_seconds=120, entry_atr_pct=0.05,
        mfe_protect=_peak_armed_schedule(), mode=ManagementMode.HARVEST,
        thesis_bucket=Bucket.TREND, thesis_giveback=_giveback_params(),
    )
    assert dec.close_reason != "thesis_harvest"


# --- ③ scalp peak frac env-knob (read-fresh, no cache) ---------------------------


def test_spec3_scalp_peak_frac_default_060() -> None:
    assert _scalp_peak_frac() == 0.60
    assert _SCALP_PEAK_ARM_R == 0.25  # UNCHANGED


def test_spec3_scalp_peak_frac_reads_fresh_075(monkeypatch) -> None:
    # Injected via env WITHOUT a re-import (the live A/B knob) -> 0.75 read fresh.
    monkeypatch.setenv("POLARIS_SCALP_PEAK_FRAC", "0.75")
    assert _scalp_peak_frac() == 0.75
    monkeypatch.delenv("POLARIS_SCALP_PEAK_FRAC", raising=False)
    assert _scalp_peak_frac() == 0.60  # back to default, no stale cache


def test_spec3_scalp_giveback_threshold_follows_env(monkeypatch) -> None:
    # frac drives the give-back fire point. peak 0.30, pnl 0.20:
    #   frac 0.60 -> threshold 0.18; pnl 0.20 > 0.18 -> HOLD (above the band).
    #   frac 0.75 -> threshold 0.225; pnl 0.20 < 0.225 -> scalp_giveback fires.
    common = dict(
        side="long", entry_price=100.0, last_mid=100.20, ofi=0.3, pnl_r=0.20,
        strategy_id="micro_reversion", peak_r=0.30,
    )
    monkeypatch.delenv("POLARIS_SCALP_PEAK_FRAC", raising=False)
    assert _scalp_exit_decision(**common) is None  # 0.60 default: hold
    monkeypatch.setenv("POLARIS_SCALP_PEAK_FRAC", "0.75")
    assert _scalp_exit_decision(**common) == "scalp_giveback"  # 0.75: bank now


# --- non-regression: loss rails + asymmetry never loosened -----------------------


def test_nonreg_scalp_stop_rail_unchanged() -> None:
    assert _SCALP_STOP_R == -0.4
    # A negative pnl with an armed peak is NEVER a give-back (profit-side only); the
    # -0.4R micro-stop rail owns the loss side regardless of frac.
    assert (
        _scalp_exit_decision(
            side="long", entry_price=100.0, last_mid=99.5, ofi=0.3, pnl_r=-0.5,
            strategy_id="micro_reversion", peak_r=0.30,
        )
        == "scalp_stop"
    )


def test_nonreg_giveback_disarm_never_touches_loss_side() -> None:
    # The peak>=1.0R disarm only removes a PROFIT-side fast-close. A red position is
    # closed by the protected_bep / loser_timeout / -1.0R rail, never re-opened by
    # the disarm. A once-protected winner gone red still closes at BEP.
    prev = ExitState(
        peak_price=110.0, trough_price=98.0, stop_price=None,
        exit_state="harvest", mfe_r=1.50, mae_r=-0.5,
    )
    dec = evaluate_exit(
        prev=prev, side="long", entry_price=100.0, last_price=99.0,
        atr_pct=0.05, pnl_r=-0.2, held_seconds=120, entry_atr_pct=0.05,
        mfe_protect=_peak_armed_schedule(), mode=ManagementMode.HARVEST,
        thesis_bucket=Bucket.TREND, thesis_giveback=_giveback_params(),
    )
    assert dec.close is True
    assert dec.close_reason == "protected_bep"
