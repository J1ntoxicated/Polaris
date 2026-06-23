"""flow_pressure EXIT precision — MFE-protect schedule unit tests.

[[flow_pressure_calibration_ai_2026-06-23]]: flow_pressure's avg MFE was +0.67R
but 17% of trades gave back >0.5R MFE (late exit). The MFE-protect schedule
RATCHETS the protective stop toward profit once favourable excursion appears —
BEP at +0.35R (leaves initial risk), a locked +0.25R at +0.50R — ON TOP of the
let-winners-run ATR trail (the protection-tighter is taken). EXPECTANCY, not a
throttle: size / entry side / the G6 -1.0R rail are untouched; ``None`` keeps
every existing caller byte-identical.

ENTRY=100, ATR_PCT=0.01 → atr_one=1.0, atr_r (2x)=2.0 → 1R == 2.0 in price.
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    MfeProtectSchedule,
    evaluate_exit,
    init_exit_state,
)

ENTRY = 100.0
ATR_PCT = 0.01  # atr_one = 1.0, atr_r = 2.0
# flow_pressure schedule (mirrors TickEngineConfig defaults): BEP at +0.35R,
# lock +0.25R at +0.50R. A WIDE 4.0-ATR running trail (the flow_pressure trail)
# so the protect floor is what actually protects — the bare trail sits far below.
SCHED = MfeProtectSchedule(bep_at_r=0.35, protect_at_r=0.50, lock_r=0.25)
WIDE_TRAIL = 4.0


def _fresh(side: str = "long") -> object:
    return init_exit_state(entry_price=ENTRY, side=side)


# --- BEP rung: at +0.35R MFE the stop leaves initial risk (floors at entry) ---


def test_protect_floors_stop_at_bep_at_035r_long() -> None:
    # peak 100.7 → mfe 0.35R. Wide 4-ATR trail = 100.7 - 4.0 = 96.7 (below
    # entry); the BEP floor lifts the stop to entry (100.0) — initial risk gone.
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.7,
        atr_pct=ATR_PCT, pnl_r=0.35, held_seconds=10,
        trail_mult=WIDE_TRAIL, mfe_protect=SCHED,
    )
    assert d.state.mfe_r >= 0.35
    assert d.state.stop_price == ENTRY  # floored to BEP, not the 96.7 wide trail


def test_protect_floors_stop_at_bep_at_035r_short() -> None:
    # Mirror: peak (trough) 99.3 → mfe 0.35R short; floor at entry (100.0).
    d = evaluate_exit(
        prev=_fresh("short"), side="short", entry_price=ENTRY, last_price=99.3,
        atr_pct=ATR_PCT, pnl_r=0.35, held_seconds=10,
        trail_mult=WIDE_TRAIL, mfe_protect=SCHED,
    )
    assert d.state.mfe_r >= 0.35
    assert d.state.stop_price == ENTRY


# --- Protect rung: at +0.50R MFE a meaningful positive R is locked ------------


def test_protect_locks_positive_r_at_050r_long() -> None:
    # peak 101.0 → mfe 0.50R. Floor = entry + lock_r*atr_r = 100 + 0.25*2 = 100.5.
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=101.0,
        atr_pct=ATR_PCT, pnl_r=0.50, held_seconds=10,
        trail_mult=WIDE_TRAIL, mfe_protect=SCHED,
    )
    assert d.state.mfe_r >= 0.50
    assert d.state.stop_price == ENTRY + SCHED.lock_r * 2.0  # 100.5


def test_protect_locks_positive_r_at_050r_short() -> None:
    d = evaluate_exit(
        prev=_fresh("short"), side="short", entry_price=ENTRY, last_price=99.0,
        atr_pct=ATR_PCT, pnl_r=0.50, held_seconds=10,
        trail_mult=WIDE_TRAIL, mfe_protect=SCHED,
    )
    assert d.state.mfe_r >= 0.50
    assert d.state.stop_price == ENTRY - SCHED.lock_r * 2.0  # 99.5


# --- Give-back is cut: reach +0.5R then decay → exit at the locked stop --------


def test_reaches_050r_then_decay_closes_near_peak_not_full_giveback() -> None:
    # Tick 1: peak 101.0 (+0.5R) → stop locked at 100.5 (+0.25R protected).
    d1 = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=101.0,
        atr_pct=ATR_PCT, pnl_r=0.50, held_seconds=10,
        trail_mult=WIDE_TRAIL, mfe_protect=SCHED,
    )
    assert d1.close is False
    assert d1.state.stop_price == 100.5
    # Tick 2: price decays to 100.4 (below the 100.5 lock) → ATR-trail-stop close
    # at the LOCKED positive-R stop, NOT a full give-back to BEP / the wide trail.
    d2 = evaluate_exit(
        prev=d1.state, side="long", entry_price=ENTRY, last_price=100.4,
        atr_pct=ATR_PCT, pnl_r=0.20, held_seconds=20,
        trail_mult=WIDE_TRAIL, mfe_protect=SCHED,
    )
    assert d2.close is True
    assert d2.close_reason == "atr_trail_stop"
    assert d2.state.stop_price == 100.5  # closed protecting +0.25R, not at BEP


# --- Ratchet integrity: the protect floor never LOOSENS a tighter prior stop --


def test_protect_floor_never_loosens_prior_tighter_stop() -> None:
    # A prior stop already at 100.8 (tighter than the +0.5R floor 100.5); the
    # schedule must not pull it DOWN to 100.5 — the ratchet holds the tighter one.
    from polaris.core.live_recalc.exit_engine import EXIT_STATE_PROTECTED, ExitState

    prev = ExitState(
        peak_price=101.0, trough_price=ENTRY, stop_price=100.8,
        exit_state=EXIT_STATE_PROTECTED,
    )
    d = evaluate_exit(
        prev=prev, side="long", entry_price=ENTRY, last_price=101.0,
        atr_pct=ATR_PCT, pnl_r=0.50, held_seconds=10,
        trail_mult=WIDE_TRAIL, mfe_protect=SCHED,
    )
    assert d.state.stop_price == 100.8  # tighter prior stop preserved


# --- None schedule is byte-identical to the pre-feature behaviour -------------


def test_none_schedule_keeps_wide_trail_unchanged() -> None:
    # Without a schedule, the +0.35R peak keeps the bare wide trail (96.7) — no
    # protect floor → byte-identical to every existing caller.
    d = evaluate_exit(
        prev=_fresh("long"), side="long", entry_price=ENTRY, last_price=100.7,
        atr_pct=ATR_PCT, pnl_r=0.35, held_seconds=10,
        trail_mult=WIDE_TRAIL, mfe_protect=None,
    )
    assert d.state.stop_price == 100.7 - WIDE_TRAIL * 1.0  # 96.7, the bare trail
