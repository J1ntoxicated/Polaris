"""#26 precise-exit engine — pure FSM / ATR-trail / loser-timeout unit tests.

EXPECTANCY (let winners run, cut dead losers), NOT a defensive throttle:
- no size change, no entry block, no P&L/strategy/bot halt — per-position only.
- The G6 -1.0R hard stop_hit rail stays in the orchestrator (not here).
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    EXIT_ATR_TRAIL_MULT,
    EXIT_LOSER_TIMEOUT_SEC,
    EXIT_STATE_HARVEST,
    EXIT_STATE_PROTECTED,
    EXIT_STATE_TOUCHED,
    ExitState,
    evaluate_exit,
    init_exit_state,
)

ENTRY = 100.0
ATR_PCT = 0.01  # 1% → atr_one = 1.0, atr_r (2x) = 2.0


def _fresh(side: str = "long") -> ExitState:
    return init_exit_state(entry_price=ENTRY, side=side)


# --- Excursion / peak tracking ---------------------------------------------


def test_peak_tracks_high_water_mark_long() -> None:
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=10,
    )
    assert d.state.peak_price == 104.0
    # price retraces but peak stays.
    d2 = evaluate_exit(
        prev=d.state, side="long", entry_price=ENTRY, last_price=102.0,
        atr_pct=ATR_PCT, pnl_r=1.0, held_seconds=20,
    )
    assert d2.state.peak_price == 104.0
    assert d2.state.trough_price == ENTRY  # never went below entry


def test_mfe_mae_r_units_long() -> None:
    st = _fresh("long")
    # up to 104 (favourable +4 / atr_r 2 = +2R), down to 99 later (adverse).
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=10,
    )
    assert d.state.mfe_r == 2.0
    assert d.state.mae_r == 0.0


def test_mfe_mae_short() -> None:
    st = _fresh("short")
    d = evaluate_exit(
        prev=st, side="short", entry_price=ENTRY, last_price=96.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=10,
    )
    # short favourable = entry - trough = 4 / 2 = +2R.
    assert d.state.mfe_r == 2.0
    assert d.state.trough_price == 96.0


# --- ATR trailing stop ratchets up not down --------------------------------


def test_atr_stop_ratchets_up_never_down_long() -> None:
    st = _fresh("long")
    # peak 103 (MFE 1.5R → PROTECTED, NOT harvest) → stop = 103 - 2*1 = 101.
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=103.0,
        atr_pct=ATR_PCT, pnl_r=1.5, held_seconds=10,
    )
    assert d.state.stop_price is not None
    assert d.state.stop_price == 103.0 - EXIT_ATR_TRAIL_MULT * 1.0
    first_stop = d.state.stop_price
    # price drops to 102.5 (no new peak, above stop) → stop must NOT loosen.
    d2 = evaluate_exit(
        prev=d.state, side="long", entry_price=ENTRY, last_price=102.5,
        atr_pct=ATR_PCT, pnl_r=1.25, held_seconds=20,
    )
    assert d2.state.stop_price == first_stop  # ratcheted, not loosened


def test_atr_stop_advances_with_new_peak_long() -> None:
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=105.0,
        atr_pct=ATR_PCT, pnl_r=2.5, held_seconds=10,
    )
    d2 = evaluate_exit(
        prev=d.state, side="long", entry_price=ENTRY, last_price=108.0,
        atr_pct=ATR_PCT, pnl_r=4.0, held_seconds=20,
    )
    assert d2.state.stop_price is not None
    assert d2.state.stop_price > d.state.stop_price  # ratcheted up


def test_atr_stop_touched_closes_long() -> None:
    st = _fresh("long")
    # peak 105 → stop 103; then price gaps to 102 → stop touched.
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=105.0,
        atr_pct=ATR_PCT, pnl_r=2.5, held_seconds=10,
    )
    d2 = evaluate_exit(
        prev=d.state, side="long", entry_price=ENTRY, last_price=102.0,
        atr_pct=ATR_PCT, pnl_r=1.0, held_seconds=20,
    )
    assert d2.close is True
    assert d2.close_reason == "atr_trail_stop"


# --- FSM advances on MFE ----------------------------------------------------


def test_fsm_advances_on_mfe() -> None:
    st = _fresh("long")
    # +0.5R → TOUCHED (peak 101 → mfe 0.5R).
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=101.0,
        atr_pct=ATR_PCT, pnl_r=0.5, held_seconds=10,
    )
    assert d.state.exit_state == EXIT_STATE_TOUCHED
    # +1.0R → PROTECTED (peak 102).
    d = evaluate_exit(
        prev=d.state, side="long", entry_price=ENTRY, last_price=102.0,
        atr_pct=ATR_PCT, pnl_r=1.0, held_seconds=20,
    )
    assert d.state.exit_state == EXIT_STATE_PROTECTED
    # +2.0R → HARVEST (peak 104).
    d = evaluate_exit(
        prev=d.state, side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=30,
    )
    assert d.state.exit_state == EXIT_STATE_HARVEST


def test_fsm_never_regresses() -> None:
    st = ExitState(
        peak_price=104.0, trough_price=ENTRY, stop_price=102.0,
        exit_state=EXIT_STATE_HARVEST,
    )
    # MFE collapses (price back to entry) but state must not regress.
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=100.5,
        atr_pct=ATR_PCT, pnl_r=0.25, held_seconds=40,
    )
    assert d.state.exit_state == EXIT_STATE_HARVEST


# --- PROTECTED_BEP closes a round-tripped winner ---------------------------


def test_protected_bep_closes_round_tripped_winner() -> None:
    # Was PROTECTED (touched +1R), now pnl dips below entry → close BEP.
    st = ExitState(
        peak_price=102.0, trough_price=ENTRY, stop_price=98.0,
        exit_state=EXIT_STATE_PROTECTED,
    )
    # last_price 99.5 keeps it above the 98 stop (so stop not touched), but
    # pnl_r is negative → protected break-even close fires.
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.5,
        atr_pct=ATR_PCT, pnl_r=-0.25, held_seconds=50,
    )
    assert d.close is True
    assert d.close_reason == "protected_bep"


def test_protected_bep_does_not_fire_when_open() -> None:
    # Still OPEN (never reached +1R) → no BEP close even if pnl negative.
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.9,
        atr_pct=ATR_PCT, pnl_r=-0.05, held_seconds=50,
    )
    assert d.close_reason != "protected_bep"


# --- Loser timeout closes a stale loser but NOT a winner -------------------


def test_loser_timeout_closes_stale_loser() -> None:
    st = _fresh("long")
    # never touched profit, losing, held beyond the timeout → close.
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.8,
        atr_pct=ATR_PCT, pnl_r=-0.1,
        held_seconds=int(EXIT_LOSER_TIMEOUT_SEC) + 1,
    )
    assert d.close is True
    assert d.close_reason == "loser_timeout"


def test_loser_timeout_does_not_close_winner() -> None:
    # held long, but currently winning → NOT a stale loser.
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=100.5,
        atr_pct=ATR_PCT, pnl_r=0.25,
        held_seconds=int(EXIT_LOSER_TIMEOUT_SEC) + 100,
    )
    assert d.close is False


def test_loser_timeout_peak_extension() -> None:
    # touched profit once (TOUCHED), now losing & past the BASE timeout but
    # within the extended window → must NOT close (earned more rope).
    st = ExitState(
        peak_price=101.0, trough_price=ENTRY, stop_price=99.0,
        exit_state=EXIT_STATE_TOUCHED,
    )
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.5,
        atr_pct=ATR_PCT, pnl_r=-0.25,
        held_seconds=int(EXIT_LOSER_TIMEOUT_SEC) + 1,
    )
    # touched_profit path → loser_timeout branch is skipped entirely.
    assert d.close_reason != "loser_timeout"


def test_loser_timeout_not_fired_before_age() -> None:
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.8,
        atr_pct=ATR_PCT, pnl_r=-0.1, held_seconds=10,
    )
    assert d.close is False
