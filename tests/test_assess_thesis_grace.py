"""Grace-period + sustained-adverse gate on the adaptive thesis re-map.

ROOT CAUSE (live, PID 351): a JUST-OPENED position opens on positive OFI; the
very next tick's OFI noise opposes → ``_assess_health`` returned BROKEN → CUT →
``thesis_cut`` fast-close at 0-1s hold (pnl_r 0). The thesis was judged on a
SINGLE fresh tick with no time/aging guard.

flow_not_block — this is EXIT-TIMING precision: let a fresh thesis ESTABLISH
before judging it. NEVER a throttle / size-cut / entry-block. EXPECTANCY.

Two independent gates (both pure, in ``assess_thesis``):
1. GRACE — before ``POLARIS_EXIT_THESIS_GRACE_SEC`` of held time, a position can
   NOT be CUT or thesis-HARVESTed (the fast closing modes). It HOLDs (or at most a
   non-closing LET_RUN) regardless of a momentary BROKEN read.
2. SUSTAINED — BROKEN requires the adverse OFI/momentum to PERSIST beyond a small
   deadband AND/OR be confirmed over a consecutive count, so a single noisy tick
   never flips a fresh winner to BROKEN.

AGED positions (past grace, sustained adverse) still CUT / HARVEST — the engine
is gated, not gutted.
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    Bucket,
    ManagementMode,
    ThesisGivebackParams,
    assess_thesis,
)

GB = ThesisGivebackParams(arm_r=0.30, frac=0.50, hard_frac=0.60)

# Grace default (~25s) and broken-ticks default (2 consecutive) — mirror the env
# defaults so the tests pin the calibration, not a magic literal at the call.
GRACE_SEC = 25
AGED = GRACE_SEC + 5  # comfortably past grace
FRESH = 1  # the 0-1s instant-cut window


def _assess(**kw: object) -> ManagementMode:
    base: dict[str, object] = dict(
        side="long",
        bucket=Bucket.TREND,
        mfe_r=0.0,
        mae_r=0.0,
        pnl_r=0.0,
        momentum_drift=0.0,
        atr_slope=0.0,
        ofi=None,
        flow_confirmed=None,
        regime="trend",
        entry_regime="trend",
        held_seconds=AGED,
        horizon_seconds=600,
        giveback=GB,
        broken_streak=99,  # default tests opt INTO sustained (already confirmed)
    )
    base.update(kw)
    return assess_thesis(**base)  # type: ignore[arg-type]


# --- GRACE: a 0-1s-old position with a momentary BROKEN read HOLDS ----------


def test_fresh_position_momentary_broken_does_not_cut() -> None:
    # The exact live regression: just opened, next-tick OFI noise opposes the long
    # + flat/red → would be BROKEN+red+CUT, but inside grace it must NOT close.
    m = _assess(
        held_seconds=FRESH, mfe_r=0.0, pnl_r=-0.1,
        momentum_drift=-0.6, ofi=-0.5, flow_confirmed=False,
        broken_streak=99,
    )
    assert m is not ManagementMode.CUT
    assert m is not ManagementMode.HARVEST


def test_fresh_position_at_grace_boundary_still_protected() -> None:
    # held_seconds exactly AT the grace floor is still inside grace (strict >).
    m = _assess(
        held_seconds=GRACE_SEC, mfe_r=0.0, pnl_r=-0.2,
        momentum_drift=-0.7, ofi=-0.6, flow_confirmed=False,
    )
    assert m is not ManagementMode.CUT


def test_fresh_green_confirmed_trend_may_let_run_inside_grace() -> None:
    # Grace only forbids the CLOSING modes — a confirmed green winner may still
    # LET_RUN (non-closing) inside grace. flow_not_block: let it establish + run.
    m = _assess(
        held_seconds=FRESH, mfe_r=0.5, pnl_r=0.4,
        momentum_drift=0.5, ofi=0.4, flow_confirmed=True,
    )
    assert m in (ManagementMode.LET_RUN, ManagementMode.HOLD)


def test_fresh_giveback_hard_does_not_fast_close_inside_grace() -> None:
    # Even a hard give-back (orthogonal HARVEST) is suppressed to a non-closing
    # decision inside grace — no thesis_harvest fast-close at 0-1s.
    m = _assess(
        held_seconds=FRESH, mfe_r=1.0, pnl_r=0.3,
        momentum_drift=0.5, ofi=0.5, flow_confirmed=True,
    )
    assert m is not ManagementMode.HARVEST
    assert m is not ManagementMode.CUT


# --- AGED: past grace + SUSTAINED adverse still CUTs / HARVESTs --------------


def test_aged_sustained_broken_red_still_cuts() -> None:
    # past grace, sustained (streak confirmed) broken + red → CUT still fires.
    m = _assess(
        held_seconds=AGED, mfe_r=0.1, pnl_r=-0.4,
        momentum_drift=-0.6, ofi=-0.5, flow_confirmed=False,
        broken_streak=99,
    )
    assert m is ManagementMode.CUT


def test_aged_fading_winner_still_harvests() -> None:
    # past grace, was green, momentum flat + OFI decayed → FADING → HARVEST.
    m = _assess(
        held_seconds=AGED, mfe_r=0.6, pnl_r=0.3,
        momentum_drift=0.0, atr_slope=-0.05, ofi=0.0, flow_confirmed=False,
    )
    assert m is ManagementMode.HARVEST


def test_aged_giveback_hard_still_harvests() -> None:
    m = _assess(
        held_seconds=AGED, mfe_r=1.0, pnl_r=0.3,
        momentum_drift=0.5, ofi=0.5, flow_confirmed=True,
    )
    assert m is ManagementMode.HARVEST


# --- SUSTAINED: a single noisy tick (no streak) never breaks an aged winner --


def test_aged_single_noisy_tick_no_streak_does_not_cut() -> None:
    # past grace BUT the broken read is a SINGLE tick (streak 0, not yet
    # confirmed): 1-tick OFI noise must NOT flip the position to BROKEN→CUT.
    m = _assess(
        held_seconds=AGED, mfe_r=0.0, pnl_r=-0.1,
        momentum_drift=-0.6, ofi=-0.5, flow_confirmed=False,
        broken_streak=0,
    )
    assert m is not ManagementMode.CUT


def test_aged_tiny_adverse_within_deadband_not_broken() -> None:
    # past grace, streak confirmed, BUT the adverse OFI/momentum magnitude is
    # within the small deadband → noise, not a real break → not CUT.
    m = _assess(
        held_seconds=AGED, mfe_r=0.0, pnl_r=-0.05,
        momentum_drift=-1e-4, ofi=-1e-4, flow_confirmed=None,
        broken_streak=99,
    )
    assert m is not ManagementMode.CUT


def test_aged_sustained_adverse_beyond_deadband_breaks() -> None:
    # past grace, streak confirmed, magnitude beyond the deadband → real break.
    m = _assess(
        held_seconds=AGED, mfe_r=0.0, pnl_r=-0.3,
        momentum_drift=-0.5, ofi=-0.4, flow_confirmed=False,
        broken_streak=99,
    )
    assert m is ManagementMode.CUT


# --- Totality: new inputs degrade safe -------------------------------------


def test_none_held_seconds_degrades_safe() -> None:
    # held_seconds None must not raise; treated as inside-grace (safest — never
    # an instant cut on an unknown age).
    m = assess_thesis(
        side="long", bucket=Bucket.TREND, mfe_r=0.0, mae_r=0.0, pnl_r=-0.4,
        momentum_drift=-0.6, atr_slope=0.0, ofi=-0.5, flow_confirmed=False,
        regime="trend", entry_regime="trend", held_seconds=None,
        horizon_seconds=600, giveback=GB,
    )
    assert m is not ManagementMode.CUT


def test_broken_streak_defaults_when_omitted() -> None:
    # broken_streak is optional; omitting it must not raise (back-compat with the
    # existing assess_thesis callers / tests).
    m = assess_thesis(
        side="long", bucket=Bucket.TREND, mfe_r=1.0, mae_r=0.0, pnl_r=0.8,
        momentum_drift=0.5, atr_slope=0.2, ofi=0.4, flow_confirmed=True,
        regime="trend", entry_regime="trend", held_seconds=AGED,
        horizon_seconds=600, giveback=GB,
    )
    assert isinstance(m, ManagementMode)
