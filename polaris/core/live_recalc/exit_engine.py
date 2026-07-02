"""Layer 6 — precise-exit engine (#26 EXPECTANCY, not a defensive throttle).

Jin's #1 loss-defense: 손실방어 = 정밀 엑싯 (adaptive stop / timing), NOT size
reduction and NOT entry blocking. This module is pure per-position exit math —
it lets winners run (ATR-anchored trailing stop + MFE-driven harvest FSM) and
cuts dead losers (round-trip break-even + stale-loser timeout). It never:

* reduces position size (T4 sizing chain is untouched / OUTSIDE this module),
* blocks or vetoes an entry (entry-side ``flow_not_block`` unchanged),
* adds a P&L / strategy / bot halt (per-position close decisions only).

The G6 hard ``stop_hit`` rail (pnl_r <= -1.0R) is the catastrophic backstop and
stays in the orchestrator — these precise exits ADD on top of it.

Pure + unit-testable: no I/O. The tick loop reads tracked state from the
``positions`` row, calls these helpers, and persists the returned state back.

This module is the FSM / excursion / trailing-stop CORE plus the package facade:
the env-tunable parameters live in ``exit_params``, the state/decision/thesis
types in ``exit_types``, and the adaptive-thesis assessment in ``exit_thesis``.
Every public symbol those modules export is RE-EXPORTED here so the long-standing
``polaris.core.live_recalc.exit_engine`` import surface is unchanged.

Trading parameters (CONSERVATIVE defaults adopted from the auto_invasion
``exit_fsm`` reference; env-overridable; FLAGGED as pending /debate calibration);
see ``exit_params`` for each knob's definition and rationale.
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_params import (
    _ATR_PCT_RELATIVE_FLOOR,
    _ATR_USD_FLOOR,
    _EXCURSION_R_CAP,
    _STATE_RANK,
    EXIT_ADAPTIVE_THESIS_ON,
    EXIT_ATR_TRAIL_MULT,
    EXIT_BAR_MFE_BEP_R,
    EXIT_BAR_MFE_LOCK_R,
    EXIT_BAR_MFE_PROTECT_R,
    EXIT_EQUITY_MFE_BEP_R,
    EXIT_EQUITY_MFE_LOCK_R,
    EXIT_EQUITY_MFE_PROTECT_R,
    EXIT_FSM_HARVEST_R,
    EXIT_FSM_PROTECT_R,
    EXIT_FSM_TOUCH_R,
    EXIT_HARVEST_TRAIL_MULT,
    EXIT_LETRUN_HARVEST_TRAIL_MULT,
    EXIT_LETRUN_TRAIL_MULT,
    EXIT_LOSER_TIMEOUT_EXT_MULT,
    EXIT_LOSER_TIMEOUT_SEC,
    EXIT_PEAK_GIVEBACK_DISARM_R,
    EXIT_PEAK_LOCK_ARM_R,
    EXIT_PEAK_LOCK_FRAC,
    EXIT_STATE_HARVEST,
    EXIT_STATE_OPEN,
    EXIT_STATE_PROTECTED,
    EXIT_STATE_TOUCHED,
    EXIT_THESIS_BREAK_HOLD_FRAC,
    EXIT_THESIS_BROKEN_TICKS,
    EXIT_THESIS_DEADBAND,
    EXIT_THESIS_DRIFT_FLOOR,
    EXIT_THESIS_DRIFT_FLOOR_RATIO,
    EXIT_THESIS_GIVEBACK_ARM_R,
    EXIT_THESIS_GIVEBACK_FRAC,
    EXIT_THESIS_GIVEBACK_HARD_FRAC,
    EXIT_THESIS_GRACE_SEC,
    drift_floor_for_timeframe,
)
from polaris.core.live_recalc.exit_thesis import (
    assess_thesis,
    bucket_from_correlation_group,
    mode_to_exit_params,
    tick_micro_broken,
)
from polaris.core.live_recalc.exit_types import (
    Bucket,
    ExitDecision,
    ExitState,
    ManagementMode,
    MfeProtectSchedule,
    ThesisExitParams,
    ThesisGivebackParams,
    ThesisHealth,
    mfe_protect_from_dict,
    mfe_protect_to_dict,
)


def _atr_one_usd(*, entry_price: float, atr_pct: float) -> float:
    """One-ATR distance in price terms (relative floor, finite)."""
    return max(
        entry_price * max(atr_pct, 0.0),
        entry_price * _ATR_PCT_RELATIVE_FLOOR,
        _ATR_USD_FLOOR,
    )


def _atr_r_usd(
    *, entry_price: float, atr_pct: float, stop_atr_mult: float = 2.0
) -> float:
    """R-unit denominator in price terms — matches the realised-PnL path
    (``entry_price * atr_pct * stop_atr_mult`` with the same stop convention as
    ``compute_unrealized_pnl_r`` / ``real_pnl_r_from_fills``).

    ``stop_atr_mult`` defaults to 2.0 (the module SSOT convention) → byte-identical
    for every existing caller. The weekend OKX makers thread 3.0 (FIX-EXIT,
    [[weekend_maker_honest_rerun_2026-06-28]]): a WIDER R-unit ATR distance — the
    −1.0R rail COEFFICIENT (in the G6 monitor) is untouched; only the denominator
    the excursion is measured against widens."""
    return max(
        entry_price * max(atr_pct, 0.0) * stop_atr_mult,
        entry_price * _ATR_PCT_RELATIVE_FLOOR,
        _ATR_USD_FLOOR,
    )


def init_exit_state(*, entry_price: float, side: str) -> ExitState:
    """Seed extremes at entry; no stop yet (set on first ratchet)."""
    return ExitState(
        peak_price=entry_price,
        trough_price=entry_price,
        stop_price=None,
        exit_state=EXIT_STATE_OPEN,
    )


def _next_fsm_state(current: str, mfe_r: float) -> str:
    """Advance the FSM by MFE (monotone — never regress)."""
    if mfe_r >= EXIT_FSM_HARVEST_R:
        target = EXIT_STATE_HARVEST
    elif mfe_r >= EXIT_FSM_PROTECT_R:
        target = EXIT_STATE_PROTECTED
    elif mfe_r >= EXIT_FSM_TOUCH_R:
        target = EXIT_STATE_TOUCHED
    else:
        target = EXIT_STATE_OPEN
    cur_rank = _STATE_RANK.get(current, 0)
    tgt_rank = _STATE_RANK.get(target, 0)
    return target if tgt_rank > cur_rank else current


def _trailing_stop(
    *, side: str, anchor_extreme: float, atr_one: float, trail_mult: float,
    prev_stop: float | None,
) -> float:
    """ATR-anchored stop that only ratchets TOWARD profit (never loosens).

    Long: ``peak - trail_mult*ATR``, clamped up to ``max(prev_stop, ...)``.
    Short: ``trough + trail_mult*ATR``, clamped down to ``min(prev_stop, ...)``.
    """
    if side == "long":
        candidate = anchor_extreme - trail_mult * atr_one
        return candidate if prev_stop is None else max(prev_stop, candidate)
    candidate = anchor_extreme + trail_mult * atr_one
    return candidate if prev_stop is None else min(prev_stop, candidate)


def evaluate_exit(
    *,
    prev: ExitState,
    side: str,
    entry_price: float,
    last_price: float,
    atr_pct: float,
    pnl_r: float,
    held_seconds: int,
    loser_timeout_sec: float | None = None,
    profit_target_r: float | None = None,
    entry_atr_pct: float | None = None,
    trail_atr_pct: float | None = None,
    trail_mult: float | None = None,
    mfe_protect: MfeProtectSchedule | None = None,
    mode: ManagementMode | None = None,
    thesis_bucket: Bucket = Bucket.TREND,
    thesis_giveback: ThesisGivebackParams | None = None,
    stop_atr_mult: float = 2.0,
) -> ExitDecision:
    """Advance excursion + stop + FSM for one position; decide close-or-hold.

    Returns the NEW ``ExitState`` (to persist) and whether a precise exit
    fired. This NEVER changes size and NEVER blocks an entry — close-or-hold of
    THIS position only. The G6 -1.0R hard stop_hit rail stays in the caller.

    ``loser_timeout_sec`` (Component B): the stale-loser timeout floor for THIS
    position, scaled to its strategy timeframe by the caller (a 1H thesis is not
    force-closed at the flat 900s). ``None`` keeps the flat
    ``EXIT_LOSER_TIMEOUT_SEC`` default (fast strategies stay short). This only
    moves the TIMEOUT horizon — the ATR-trailing stop and the protected-BEP
    exit (the precise exits) are untouched.

    ``profit_target_r``: a fixed take-profit in R for mean-reversion strategies
    (set per-strategy from ``StrategyMetadata.profit_target_r``). When set and
    favourable excursion reaches it, the position is HARVESTED immediately —
    BEFORE the wide let-winners-run ATR trail can give a bounded revert-to-mean
    gain back (a BB fade reverts extreme→middle ≈ 1 R, then bounces; the 2-ATR
    trail would round-trip the whole move). ``None`` (default) keeps the trend
    exit for every momentum strategy — byte-identical. EXPECTANCY, not a
    throttle: a per-position close target only — size / entry side / halt rail
    untouched.

    ``entry_atr_pct``: the ENTRY-TIME ATR anchor for the R-unit DENOMINATOR
    (mfe_r/mae_r + the FSM thresholds they drive). The per-tick recomputed
    ``atr_pct`` shrank during volatility contraction and inflated excursions
    4-8x; anchoring pins the measuring unit at the entry-time risk. The TRAIL
    width intentionally keeps the CURRENT ``atr_pct`` — a Chandelier trail
    tracks today's noise band and the ratchet already forbids loosening.
    ``None`` (legacy rows / callers) keeps the current-ATR denominator —
    byte-identical to the pre-anchor behaviour.

    ``trail_atr_pct``: the STABLE bar-scale ATR for the TRAIL-WIDTH only
    ([[g7_tick_trail_atr_scale_2026-06-25]]). The TICK exit pass feeds ``atr_pct``
    the seconds-scale ``_window_atr_pct`` (mid-range over the few-second feature
    window) — fine as a live mark, but ~10x narrower than the bar ATR the trail
    multipliers (2-4x) were calibrated against, so the bare trail sat micro-tight
    and clipped fresh winners at ~flat on the next sub-tick reversal (live:
    atr_trail_stop cadence='tick' avg hold 16.6s, +0.14R MFE, realised ~0R). When
    set, the trail width ``atr_one`` is computed from ``trail_atr_pct`` instead of
    ``atr_pct`` so the trail distance is anchored to the entry-time bar risk. The
    R DENOMINATOR (``entry_atr_pct``), the FSM rungs, the peak-fraction floor and
    the G6 -1.0R rail are UNTOUCHED. ``None`` (every existing caller, incl. the bar
    recalc) keeps ``atr_pct`` for the trail — byte-identical; the bar Chandelier
    still tracks today's noise band intentionally. EXPECTANCY (let the winner run),
    not a throttle: it only WIDENS the tick trail to the correct scale; the ratchet
    still forbids loosening an already-set stop.

    ``trail_mult``: per-position override of the let-winners-run ATR-trail width
    (in ATR units). ``None`` keeps the module default ``EXIT_ATR_TRAIL_MULT``
    (=2.0) — byte-identical for every existing caller. A WIDER value lets a
    favourable drift run further before the trail closes it: the tick engine
    passes a wider trail for ``flow_pressure`` momentum positions so OFI drift is
    captured past the old ~75s scalp (honest fee-net retune w02ccvq0q — the
    longer-hold cohort was the only gross-cost-positive bucket). EXPECTANCY, not
    a throttle: it only LOOSENS the trail (lets the winner run); the ratchet still
    forbids loosening the already-set stop, the protected-BEP exit + loser-timeout
    + the G6 -1.0R hard rail (the loss-defence) are all untouched. HARVEST still
    tightens to ``EXIT_HARVEST_TRAIL_MULT`` once a big winner is locked.

    ``mfe_protect`` (flow_pressure EXIT precision,
    [[flow_pressure_calibration_ai_2026-06-23]]): an MFE-driven protective-stop
    schedule that RATCHETS the stop toward profit once favourable excursion
    appears (BEP at ``bep_at_r``, lock ``lock_r`` positive R at ``protect_at_r``).
    The protection-tighter of the ATR trail and the schedule floor is taken, so it
    only ever tightens — capturing the +0.67R avg MFE and cutting the 17% >0.5R
    give-back. ``None`` (every existing caller) → byte-identical. EXPECTANCY, not
    a throttle: size / entry side / the G6 -1.0R rail untouched; it cannot loosen
    the trail (the ratchet below still maxes/mins against the prior stop).

    ``mode`` (adaptive thesis re-map, [[adaptive_thesis_remap_2026-06-23]]): a
    ``ManagementMode`` from ``assess_thesis`` that RE-MAPS this position's exit
    schedule from entry-thesis health. ``None`` (default) → byte-identical for
    EVERY existing caller (no override applied, no thesis close checked). When set,
    ``mode_to_exit_params`` resolves trail_mult / mfe_protect / profit_target
    OVERRIDES (the mode tuple wins over the same-named caller kwargs) and the
    thesis_harvest / thesis_cut fast-closes are checked at the TOP of the close
    ladder (before the existing reasons). EXIT timing only — LET_RUN widens (winner
    runs), HARVEST tightens/banks, CUT closes an invalidated (broken+red) position,
    REMODE swaps the trend<->range schedule; size / entry / the G6 -1.0R rail
    untouched. ``thesis_bucket`` / ``thesis_giveback`` feed the param mapping (a
    None giveback degrades to the module defaults).

    ``stop_atr_mult`` (FIX-EXIT, [[weekend_maker_honest_rerun_2026-06-28]]): the
    R-unit ATR multiplier that denominates mfe_r / mae_r (the excursion FSM ruler).
    ``2.0`` (default) is the module SSOT convention → byte-identical for every
    existing caller. The two validated weekend OKX makers thread ``3.0`` (the
    R-unit ATR distance WIDENS) so a fixed-$ maker fee shrinks as a fraction of R
    and the bounded revert has more room. 🚨 EXIT-MEASURE only: the −1.0R rail
    COEFFICIENT (max_loss_r in the G6 monitor) and the size / entry side are
    UNTOUCHED — a wider ATR unit simply makes the SAME rung a wider price stop.
    """
    # 1. Update price extremes (running peak / trough over the position life).
    peak = max(prev.peak_price, last_price)
    trough = min(prev.trough_price, last_price)

    # 2. Excursion in R units (favourable >= 0, adverse <= 0) — denominator
    #    anchored at entry when available; telemetry capped at |100R|.
    r_denominator_pct = atr_pct if entry_atr_pct is None else entry_atr_pct
    atr_r = _atr_r_usd(
        entry_price=entry_price, atr_pct=r_denominator_pct,
        stop_atr_mult=stop_atr_mult,
    )
    if side == "long":
        mfe_r = max(0.0, (peak - entry_price) / atr_r)
        mae_r = min(0.0, (trough - entry_price) / atr_r)
    else:
        mfe_r = max(0.0, (entry_price - trough) / atr_r)
        mae_r = min(0.0, (entry_price - peak) / atr_r)
    mfe_r = min(mfe_r, _EXCURSION_R_CAP)
    mae_r = max(mae_r, -_EXCURSION_R_CAP)

    # 2b. Adaptive thesis re-map (mode override). ``mode is None`` → NOTHING
    #     changes (the trail_mult / mfe_protect / profit_target_r below are the
    #     caller's, and the thesis closes are off) → byte-identical to every
    #     existing caller. When a mode is supplied, the mode tuple OVERRIDES the
    #     same-named exit knobs (EXIT timing only — size / entry / G6 rail
    #     untouched) and arms the thesis_harvest / thesis_cut top-of-ladder closes.
    thesis_harvest = False
    thesis_cut = False
    if mode is not None:
        giveback = (
            thesis_giveback
            if thesis_giveback is not None
            else ThesisGivebackParams(
                arm_r=EXIT_THESIS_GIVEBACK_ARM_R,
                frac=EXIT_THESIS_GIVEBACK_FRAC,
                hard_frac=EXIT_THESIS_GIVEBACK_HARD_FRAC,
            )
        )
        tp = mode_to_exit_params(
            mode, bucket=thesis_bucket, mfe_r=mfe_r, pnl_r=pnl_r,
            giveback=giveback, base_mfe_protect=mfe_protect,
            base_profit_target_r=profit_target_r,
        )
        if tp.trail_mult is not None:
            trail_mult = tp.trail_mult
        mfe_protect = tp.mfe_protect
        profit_target_r = tp.profit_target_r
        thesis_harvest = tp.thesis_harvest
        thesis_cut = tp.thesis_cut

    # 3. FSM advance by MFE (monotone — never regress).
    new_state = _next_fsm_state(prev.exit_state, mfe_r)

    # 4. ATR-trailing stop. Tighter trail once in HARVEST (locks the winner;
    #    still only ratchets toward profit — never loosens). The trail WIDTH is
    #    measured on ``trail_atr_pct`` when the caller supplies the stable bar-scale
    #    ATR (the tick path — its ``atr_pct`` is the seconds-scale window range),
    #    else on ``atr_pct`` (the bar Chandelier default — byte-identical).
    trail_atr_basis = atr_pct if trail_atr_pct is None else trail_atr_pct
    atr_one = _atr_one_usd(entry_price=entry_price, atr_pct=trail_atr_basis)
    # Let-winners-run trail width: the per-position ``trail_mult`` override (e.g.
    # a wider flow_pressure trail) when supplied, else the module default. HARVEST
    # normally tightens to EXIT_HARVEST_TRAIL_MULT (1-ATR) to lock a big winner —
    # BUT a confirmed-momentum TREND winner carrying a PEAK-FRACTION schedule must
    # NOT collapse to the 1-ATR trail (that is exactly what flat-lined the +7.33R
    # burst at break-even, [[ab_letrun_maker_2026-06-24]]). For those, HARVEST
    # holds the WIDE let-run-harvest trail and the peak-fraction floor (4b) supplies
    # the lock instead. A schedule WITHOUT a peak-arm keeps the original tighten.
    run_trail_mult = EXIT_ATR_TRAIL_MULT if trail_mult is None else trail_mult
    peak_lock_armed = (
        mfe_protect is not None and mfe_protect.peak_lock_arm_r > 0.0
    )
    if new_state == EXIT_STATE_HARVEST:
        effective_trail_mult = (
            max(EXIT_LETRUN_HARVEST_TRAIL_MULT, run_trail_mult)
            if peak_lock_armed
            else EXIT_HARVEST_TRAIL_MULT
        )
    else:
        effective_trail_mult = run_trail_mult
    # 🚨 FIX-EXIT protect-floor/trail desync ([[trade_mess_full_audit_2026-07-02]]):
    # the MFE-protect BEP rung (4b below) arms at price distance
    # ``mfe_protect.bep_at_r * atr_r`` — a distance that WIDENS with
    # ``stop_atr_mult`` (the FIX-EXIT fee-shrink R-unit ruler,
    # [[weekend_maker_honest_rerun_2026-06-28]]) — vs. the running-trail distance
    # ``effective_trail_mult * atr_one`` (no stop_atr_mult term). Divide by
    # ``effective_trail_mult`` — the mult ACTUALLY applied below — NOT the
    # pre-HARVEST ``run_trail_mult``: a REVERSION-bucket schedule
    # (``peak_lock_arm_r=0.0``) tightens to ``EXIT_HARVEST_TRAIL_MULT`` on
    # HARVEST while ``run_trail_mult`` stays wider; widening against the wrong
    # (wider) divisor under-widens ``atr_one`` and the desync reopens exactly in
    # HARVEST — the state the ladder exists to protect. Widening the trail basis
    # to the BEP arm's own raw-ATR equivalent (only when it EXCEEDS the existing
    # basis) guarantees ``trail_dist >= bep_at_r * atr_r`` — the ladder always
    # gets a chance to arm before the raw trail alone would have closed the
    # position. Rung R-THRESHOLDS (``mfe_r >= bep_at_r`` etc., derived from the
    # ``atr_r`` at step 2) are untouched — only the TRAIL's own width widens.
    # No-op (byte-identical) whenever ``mfe_protect is None`` (every caller
    # without a schedule) or the arm distance already sits inside the trail
    # (every existing SSOT / weekend-maker 2x/3x caller today).
    if mfe_protect is not None and mfe_protect.bep_at_r > 0.0:
        _bep_arm_atr_one = (mfe_protect.bep_at_r * atr_r) / effective_trail_mult
        atr_one = max(atr_one, _bep_arm_atr_one)
    anchor = peak if side == "long" else trough
    stop_price = _trailing_stop(
        side=side, anchor_extreme=anchor, atr_one=atr_one,
        trail_mult=effective_trail_mult, prev_stop=prev.stop_price,
    )

    # 4b. MFE-protect floor (flow_pressure EXIT precision): once favourable
    #     excursion reaches a rung, ratchet the stop toward profit — BEP at
    #     ``bep_at_r`` (leave initial risk), lock ``lock_r`` positive R at
    #     ``protect_at_r``. Take the protection-tighter of the ATR trail and the
    #     floor (long → higher stop, short → lower) so it ONLY tightens; the
    #     ratchet above already forbids loosening. ``None`` → unchanged. The trail
    #     basis (``atr_one``, step 4) is pre-widened above so the BEP arm distance
    #     can never outrun it — the ladder below always gets a chance to bind.
    if mfe_protect is not None:
        protect_floor: float | None = None
        if mfe_r >= mfe_protect.protect_at_r:
            offset = mfe_protect.lock_r * atr_r  # positive R locked, in price
            protect_floor = (
                entry_price + offset if side == "long" else entry_price - offset
            )
        elif mfe_r >= mfe_protect.bep_at_r:
            protect_floor = entry_price  # break-even: leave initial risk
        # Peak-fraction floor (let-winners-run, [[ab_letrun_maker_2026-06-24]]):
        # once MFE reaches the arm rung, lock a FRACTION of the REACHED peak MFE —
        # ``entry ± peak_mfe_r * frac * atr_r`` — so a confirmed big winner is held
        # near peak% instead of the small fixed lock that flat-lined the +7.33R
        # burst. PHASE composition: the fixed sub-arm rungs govern BELOW the arm /
        # the crossover; above it the peak-fraction floor takes over via the
        # tighter-of (long → max / short → min). The ``mfe_r``-driven peak is the
        # running max-MFE (the FSM never regresses it), so the floor is monotone.
        if (
            mfe_protect.peak_lock_arm_r > 0.0
            and mfe_r >= mfe_protect.peak_lock_arm_r
        ):
            peak_offset = mfe_r * mfe_protect.peak_lock_frac * atr_r
            peak_floor = (
                entry_price + peak_offset
                if side == "long"
                else entry_price - peak_offset
            )
            if protect_floor is None:
                protect_floor = peak_floor
            else:
                protect_floor = (
                    max(protect_floor, peak_floor)
                    if side == "long"
                    else min(protect_floor, peak_floor)
                )
        if protect_floor is not None:
            stop_price = (
                max(stop_price, protect_floor)
                if side == "long"
                else min(stop_price, protect_floor)
            )

    state = ExitState(
        peak_price=peak, trough_price=trough, stop_price=stop_price,
        exit_state=new_state, mfe_r=mfe_r, mae_r=mae_r,
    )

    # 5. Close decisions (precise exits — per-position only).
    #    (a-) ADAPTIVE THESIS top-of-ladder closes (checked BEFORE every existing
    #         reason). ``thesis_cut``: the entry thesis is BROKEN and the position
    #         is red/flat (assess_thesis only emits CUT then) → close NOW.
    #         ``thesis_harvest``: a HARD give-back on a once-green position → bank
    #         it NOW (near the peak). EXIT timing only; ``mode is None`` leaves both
    #         False → byte-identical.
    if thesis_cut:
        return ExitDecision(state=state, close=True, close_reason="thesis_cut")
    if thesis_harvest:
        return ExitDecision(state=state, close=True, close_reason="thesis_harvest")

    #    (a0) TAKE-PROFIT at the mean-reversion target FIRST: a strategy that
    #         opts in with a fixed ``profit_target_r`` (a BB fade) HARVESTS the
    #         instant current PnL reaches the target — before the wide
    #         let-winners-run ATR trail can round-trip a bounded revert-to-mean.
    #         ``None`` keeps every momentum strategy on the trend exit (no fixed
    #         target). EXPECTANCY — per-position close only; never size / entry /
    #         halt.
    if profit_target_r is not None and pnl_r >= profit_target_r:
        return ExitDecision(state=state, close=True, close_reason="target_harvest")

    #    (a) PROTECTED break-even FIRST: a round-tripped winner that gave it all
    #        back closes at break-even — the tighter, more precise exit takes
    #        priority over the wider ATR trail (don't wait for the trail when a
    #        once-protected winner has already round-tripped negative).
    if new_state in (EXIT_STATE_PROTECTED, EXIT_STATE_HARVEST) and pnl_r < 0.0:
        return ExitDecision(state=state, close=True, close_reason="protected_bep")

    #    (b) ATR-trailing stop touched (let-winners-run trail).
    stop_touched = (
        (side == "long" and last_price <= stop_price)
        or (side == "short" and last_price >= stop_price)
    )
    if stop_touched:
        return ExitDecision(state=state, close=True, close_reason="atr_trail_stop")

    #    (c) Stale-loser timeout — a currently-losing position past its timeout
    #        is closed. Peak-extension: a position that ONCE touched profit
    #        earned more rope, so its timeout is multiplied by
    #        EXIT_LOSER_TIMEOUT_EXT_MULT before the close fires (a never-profit
    #        loser still times out at the BASE EXIT_LOSER_TIMEOUT_SEC). Still a
    #        per-position exit — no size change, no entry block, no halt.
    touched_profit = _STATE_RANK.get(new_state, 0) > _STATE_RANK[EXIT_STATE_OPEN]
    timeout = (
        EXIT_LOSER_TIMEOUT_SEC
        if loser_timeout_sec is None
        else loser_timeout_sec
    )
    if touched_profit:
        timeout *= EXIT_LOSER_TIMEOUT_EXT_MULT
    if pnl_r < 0.0 and held_seconds > timeout:
        return ExitDecision(state=state, close=True, close_reason="loser_timeout")

    return ExitDecision(state=state, close=False, close_reason=None)


__all__ = [
    "EXIT_ADAPTIVE_THESIS_ON",
    "EXIT_ATR_TRAIL_MULT",
    "EXIT_BAR_MFE_BEP_R",
    "EXIT_BAR_MFE_LOCK_R",
    "EXIT_BAR_MFE_PROTECT_R",
    "EXIT_EQUITY_MFE_BEP_R",
    "EXIT_EQUITY_MFE_LOCK_R",
    "EXIT_EQUITY_MFE_PROTECT_R",
    "EXIT_FSM_HARVEST_R",
    "EXIT_FSM_PROTECT_R",
    "EXIT_FSM_TOUCH_R",
    "EXIT_HARVEST_TRAIL_MULT",
    "EXIT_LETRUN_HARVEST_TRAIL_MULT",
    "EXIT_LETRUN_TRAIL_MULT",
    "EXIT_LOSER_TIMEOUT_EXT_MULT",
    "EXIT_LOSER_TIMEOUT_SEC",
    "EXIT_PEAK_GIVEBACK_DISARM_R",
    "EXIT_PEAK_LOCK_ARM_R",
    "EXIT_PEAK_LOCK_FRAC",
    "EXIT_STATE_HARVEST",
    "EXIT_STATE_OPEN",
    "EXIT_STATE_PROTECTED",
    "EXIT_STATE_TOUCHED",
    "EXIT_THESIS_BREAK_HOLD_FRAC",
    "EXIT_THESIS_BROKEN_TICKS",
    "EXIT_THESIS_DEADBAND",
    "EXIT_THESIS_DRIFT_FLOOR",
    "EXIT_THESIS_DRIFT_FLOOR_RATIO",
    "EXIT_THESIS_GIVEBACK_ARM_R",
    "EXIT_THESIS_GIVEBACK_FRAC",
    "EXIT_THESIS_GIVEBACK_HARD_FRAC",
    "EXIT_THESIS_GRACE_SEC",
    "Bucket",
    "ExitDecision",
    "ExitState",
    "ManagementMode",
    "MfeProtectSchedule",
    "ThesisExitParams",
    "ThesisGivebackParams",
    "ThesisHealth",
    "assess_thesis",
    "bucket_from_correlation_group",
    "drift_floor_for_timeframe",
    "evaluate_exit",
    "init_exit_state",
    "mfe_protect_from_dict",
    "mfe_protect_to_dict",
    "mode_to_exit_params",
    "tick_micro_broken",
]
