"""#26 precise-exit engine — pure FSM / ATR-trail / loser-timeout unit tests.

EXPECTANCY (let winners run, cut dead losers), NOT a defensive throttle:
- no size change, no entry block, no P&L/strategy/bot halt — per-position only.
- The G6 -1.0R hard stop_hit rail stays in the orchestrator (not here).
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    EXIT_ATR_TRAIL_MULT,
    EXIT_LOSER_TIMEOUT_EXT_MULT,
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


def test_loser_timeout_peak_extension_survives_past_base() -> None:
    # #26 N2: touched profit once (TOUCHED), now losing & past the BASE timeout
    # but WITHIN the extended window → must NOT close yet (earned more rope).
    st = ExitState(
        peak_price=101.0, trough_price=ENTRY, stop_price=99.0,
        exit_state=EXIT_STATE_TOUCHED,
    )
    # last_price 99.5 stays above the 99.0 stop (no ATR-trail close) and the
    # FSM is only TOUCHED (no protected_bep) → only the timeout path can fire.
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.5,
        atr_pct=ATR_PCT, pnl_r=-0.25,
        held_seconds=int(EXIT_LOSER_TIMEOUT_SEC) + 1,
    )
    # Past base but below the extended window → still held.
    assert d.close is False
    assert d.close_reason is None


def test_loser_timeout_peak_extension_closes_past_extended() -> None:
    # #26 N2: the SAME profit-touched loser DOES time out once held beyond the
    # EXTENDED window (EXT_MULT × base) — peak-extension lengthens, not removes.
    st = ExitState(
        peak_price=101.0, trough_price=ENTRY, stop_price=99.0,
        exit_state=EXIT_STATE_TOUCHED,
    )
    extended = int(EXIT_LOSER_TIMEOUT_SEC * EXIT_LOSER_TIMEOUT_EXT_MULT)
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.5,
        atr_pct=ATR_PCT, pnl_r=-0.25,
        held_seconds=extended + 1,
    )
    assert d.close is True
    assert d.close_reason == "loser_timeout"


def test_never_profit_loser_times_out_at_base() -> None:
    # #26 N2 companion: a never-profit loser still closes at the BASE timeout —
    # the extension is reserved for positions that once touched profit.
    st = _fresh("long")  # OPEN, never advanced
    base_survives = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.8,
        atr_pct=ATR_PCT, pnl_r=-0.1,
        held_seconds=int(EXIT_LOSER_TIMEOUT_SEC) - 1,
    )
    assert base_survives.close is False
    base_closes = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.8,
        atr_pct=ATR_PCT, pnl_r=-0.1,
        held_seconds=int(EXIT_LOSER_TIMEOUT_SEC) + 1,
    )
    assert base_closes.close is True
    assert base_closes.close_reason == "loser_timeout"


def test_loser_timeout_not_fired_before_age() -> None:
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.8,
        atr_pct=ATR_PCT, pnl_r=-0.1, held_seconds=10,
    )
    assert d.close is False


# --- Component B: exit horizon ∝ timeframe (loser_timeout_sec override) -----


def test_loser_timeout_override_default_is_flat() -> None:
    # loser_timeout_sec=None keeps the flat EXIT_LOSER_TIMEOUT_SEC default
    # (back-compat — every existing call site is unchanged).
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.8,
        atr_pct=ATR_PCT, pnl_r=-0.1,
        held_seconds=int(EXIT_LOSER_TIMEOUT_SEC) + 1, loser_timeout_sec=None,
    )
    assert d.close is True
    assert d.close_reason == "loser_timeout"


def test_loser_timeout_long_horizon_not_closed_at_900() -> None:
    # A 1H thesis (timeout floor 7200s) losing for 901s must NOT be force-closed
    # — its horizon is respected. Stays above any stop / not protected so only
    # the timeout path could fire (and must not).
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.8,
        atr_pct=ATR_PCT, pnl_r=-0.1,
        held_seconds=int(EXIT_LOSER_TIMEOUT_SEC) + 1,  # 901s
        loser_timeout_sec=7200.0,  # 2 bars of 1H
    )
    assert d.close is False
    assert d.close_reason is None


def test_loser_timeout_long_horizon_closes_past_its_window() -> None:
    # The SAME 1H thesis DOES time out once held beyond its own (longer) window.
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=99.8,
        atr_pct=ATR_PCT, pnl_r=-0.1,
        held_seconds=7201, loser_timeout_sec=7200.0,
    )
    assert d.close is True
    assert d.close_reason == "loser_timeout"


# --- profit_target_r take-profit (mean-reversion harvest) ------------------
# A fade strategy opts in to a fixed take-profit so the engine harvests at the
# target instead of letting the wide ATR trail round-trip a bounded revert.
def test_target_harvest_long_fires_at_target() -> None:
    # +1R reached, price still near the peak (trail would NOT fire) → harvest now.
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=102.0,
        atr_pct=ATR_PCT, pnl_r=1.0, held_seconds=30, profit_target_r=1.0,
    )
    assert d.close is True
    assert d.close_reason == "target_harvest"


def test_target_harvest_short_fires_at_target() -> None:
    st = _fresh("short")
    d = evaluate_exit(
        prev=st, side="short", entry_price=ENTRY, last_price=98.0,
        atr_pct=ATR_PCT, pnl_r=1.0, held_seconds=30, profit_target_r=1.0,
    )
    assert d.close is True
    assert d.close_reason == "target_harvest"


def test_target_harvest_holds_below_target() -> None:
    # Favourable but below the target → no harvest (let it keep reverting).
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=101.0,
        atr_pct=ATR_PCT, pnl_r=0.5, held_seconds=30, profit_target_r=1.0,
    )
    assert d.close is False


def test_no_target_default_is_trend_exit() -> None:
    # profit_target_r=None (default) → a +1R winner is NOT harvested; the trend
    # exit (let-winners-run) is byte-identical to before this feature.
    st = _fresh("long")
    d = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=102.0,
        atr_pct=ATR_PCT, pnl_r=1.0, held_seconds=30,
    )
    assert d.close is False
    assert d.close_reason is None


def test_target_harvest_priority_over_trail_at_peak() -> None:
    # Position ran to +2R peak then sits at +1R: target harvests at +1R rather
    # than waiting for the 2-ATR trail to round-trip the move back to entry.
    st = _fresh("long")
    ran = evaluate_exit(
        prev=st, side="long", entry_price=ENTRY, last_price=104.0,
        atr_pct=ATR_PCT, pnl_r=2.0, held_seconds=10, profit_target_r=1.0,
    )
    # (peak push already harvests at +2R >= target; the point is target fires)
    assert ran.close is True and ran.close_reason == "target_harvest"


# --- guard: target HARVESTS before the wide trail can round-trip the revert ---
def _pnl_r(side: str, entry: float, last: float) -> float:
    atr_r = entry * ATR_PCT * 2.0
    return (entry - last) / atr_r if side == "short" else (last - entry) / atr_r


def test_target_beats_roundtrip_short_fade() -> None:
    """A short fade reverts to the mean (+1R) then bounces back to entry.

    WITH the fade target the engine harvests +1R at the mean; WITHOUT it the
    2-ATR let-winners-run trail rides the bounce back and round-trips to ~0R.
    Pins the must-fix guard: target_harvest fires BEFORE atr_trail_stop AND
    beats the status-quo net R on a representative revert→bounce path.
    """
    side = "short"
    # Tick 1: reverts down to the mean (favourable +1R for a short).
    p1 = 98.0
    with_target = evaluate_exit(
        prev=_fresh(side), side=side, entry_price=ENTRY, last_price=p1,
        atr_pct=ATR_PCT, pnl_r=_pnl_r(side, ENTRY, p1), held_seconds=30,
        profit_target_r=1.0,
    )
    assert with_target.close is True
    assert with_target.close_reason == "target_harvest"
    target_net_r = _pnl_r(side, ENTRY, p1)  # banked at the mean
    assert target_net_r >= 1.0

    # Status quo (no target): tick 1 holds, then price bounces back to entry.
    sq1 = evaluate_exit(
        prev=_fresh(side), side=side, entry_price=ENTRY, last_price=p1,
        atr_pct=ATR_PCT, pnl_r=_pnl_r(side, ENTRY, p1), held_seconds=30,
    )
    assert sq1.close is False  # the wide trail does NOT harvest the revert
    p2 = 100.0  # bounce back to entry (the round-trip the fix targets)
    sq2 = evaluate_exit(
        prev=sq1.state, side=side, entry_price=ENTRY, last_price=p2,
        atr_pct=ATR_PCT, pnl_r=_pnl_r(side, ENTRY, p2), held_seconds=60,
    )
    assert sq2.close is True and sq2.close_reason == "atr_trail_stop"
    status_quo_net_r = _pnl_r(side, ENTRY, p2)  # ~0R round-trip
    assert target_net_r > status_quo_net_r  # the fix strictly beats status quo
