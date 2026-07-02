"""[P1-8] fee-floor R-unit widen desyncs the MFE-protect floor from the trail.

DEMO/PAPER virtual funds. AGGRESSIVE / flow_not_block — this is a WINNER-PROTECTION
fix, never a throttle / entry-block / size-cut. The G6 -1.0R rail coefficient and
the R-unit ATR-DISTANCE (``stop_atr_mult``, the FIX-EXIT fee-shrinking mechanism,
[[weekend_maker_honest_rerun_2026-06-28]]) are UNTOUCHED.

ROOT CAUSE: the MFE-protect BEP rung (``mfe_protect.bep_at_r``) arms at a price
distance of ``bep_at_r * atr_r`` — a distance that WIDENS with ``stop_atr_mult``
(the FIX-EXIT fee-shrink R-unit ruler), while the running trail is a FIXED price
distance of ``trail_mult * atr_one`` (NO ``stop_atr_mult`` term — trail width is
an orthogonal Chandelier convention). At the module SSOT ``stop_atr_mult=2.0``
the BEP arm distance (``0.30R * 2 = 0.6 ATR``) always sits inside the trail
(``2.0 ATR``) — every existing caller. Once the ruler widens far enough
(``bep_at_r * stop_atr_mult > trail_mult`` — e.g. a fee-floor-widened ratio
observed ~18.3x on a floor-bound GBPUSD leg per the 2026-07-02 forensic audit:
``0.30 * 18.3 = 5.49 ATR > 2.0 ATR``) the trail ALWAYS ratchets past the BEP arm
distance on the very tick that arms it (peak and trail move together), so the
whole protect ladder (BEP / lock / peak-lock) goes silently INERT — it can never
again be the tighter-of winner once the ratchet has set past it. A winner then
gives back the FULL raw-trail width before closing, instead of the intended
graduated lock.

FIX: widen the trail's own raw-ATR basis (``atr_one``, step 4) so the trail
distance is never narrower than the BEP arm's own price distance —
``atr_one' = max(atr_one, bep_at_r * atr_r / run_trail_mult)``, applied ONLY when
a ``mfe_protect`` schedule is supplied. This guarantees, by construction, for
ANY ``stop_atr_mult``:

    BEP arm price-distance (``bep_at_r * atr_r``) <= trail price-distance

so the ladder always gets a chance to bind before the raw trail alone would
have closed the position. Rung R-THRESHOLDS (``mfe_r >= bep_at_r`` etc.,
computed upstream at step 2 from the UNCHANGED ``atr_r``) are untouched — only
the TRAIL's own width widens. No-op (byte-identical) whenever ``mfe_protect is
None`` (every caller without a schedule) or the arm distance already sits
inside the trail (every existing SSOT ``2.0x`` / weekend-maker ``3.0x`` caller
today).
"""

from __future__ import annotations

from polaris.core.live_recalc.exit_engine import (
    EXIT_ATR_TRAIL_MULT,
    EXIT_FSM_HARVEST_R,
    EXIT_STATE_HARVEST,
    ExitState,
    MfeProtectSchedule,
    evaluate_exit,
    init_exit_state,
)

ENTRY = 100.0
ATR_PCT = 0.01  # atr_one = 1.0 (real term, not floor-bound) for every mult below.
WIDE_MULT = 18.3  # illustrative fee-floor-widened R-unit ratio (forensic audit).


def _fresh(side: str = "long") -> ExitState:
    return init_exit_state(entry_price=ENTRY, side=side)


def _sched() -> MfeProtectSchedule:
    return MfeProtectSchedule(bep_at_r=0.30, protect_at_r=0.45, lock_r=0.20)


def _reversion_sched() -> MfeProtectSchedule:
    # REVERSION-bucket shape (peak_lock_arm_r=0.0) — the registered weekend-maker
    # strategies (weekend_thin_book_flush_maker / weekend_funding_capitulation_maker,
    # mean_reversion correlation group) get exactly this schedule shape from
    # ``_mfe_protect_for_strategy``. Same bep/protect/lock rungs as ``_sched()``.
    return MfeProtectSchedule(
        bep_at_r=0.30, protect_at_r=0.45, lock_r=0.20,
        peak_lock_arm_r=0.0, peak_lock_frac=0.0,
    )


# --- the invariant itself: BEP arm price-distance never exceeds the trail ----


def test_bep_arm_price_distance_never_exceeds_trail_at_wide_mult() -> None:
    # 0.30R * 18.3 = 5.49 ATR (raw rung distance) vs the default 2.0-ATR trail —
    # pre-fix the trail would already sit at peak-2.0 ATR (well past entry) by
    # the exact tick BEP arms; post-fix the trail basis widens so its OWN
    # distance from the peak is >= the BEP arm distance, i.e. the trail can
    # never have already ratcheted past where BEP would floor the stop.
    peak = ENTRY + 0.30 * ATR_PCT * ENTRY * WIDE_MULT  # mfe_r == 0.30R (arms BEP)
    d = evaluate_exit(
        prev=_fresh(), side="long", entry_price=ENTRY, last_price=peak,
        atr_pct=ATR_PCT, pnl_r=0.30, held_seconds=20,
        mfe_protect=_sched(), stop_atr_mult=WIDE_MULT,
    )
    bep_arm_dist = 0.30 * ATR_PCT * ENTRY * WIDE_MULT
    assert d.state.stop_price is not None
    # the trail (anchored at peak) must reach AT LEAST as far back as entry —
    # i.e. its distance from peak is >= the BEP arm distance from entry.
    assert peak - d.state.stop_price <= bep_arm_dist + 1e-9


def test_giveback_after_wide_ruler_peak_bounded_like_ssot_mult() -> None:
    # Reproduces the forensic-audit failure mode directly: a winner peaks to
    # +0.45R (arms the +0.20R lock) on the WIDE ruler, then retraces. Pre-fix
    # the raw 2.0-ATR trail (peak - 2*atr_one) had ALREADY ratcheted past the
    # lock floor by the exact peak tick (the trail and the arm move together),
    # so the retrace round-tripped the position out via the bare trail giving
    # back MORE than the intended lock. Post-fix the lock floor is the tighter
    # (winning) constraint, mirroring the SSOT mult=2.0 behaviour exactly.
    peak = ENTRY + 0.46 * ATR_PCT * ENTRY * WIDE_MULT
    d1 = evaluate_exit(
        prev=_fresh(), side="long", entry_price=ENTRY, last_price=peak,
        atr_pct=ATR_PCT, pnl_r=0.46, held_seconds=20,
        mfe_protect=_sched(), stop_atr_mult=WIDE_MULT,
    )
    lock_price = ENTRY + 0.20 * ATR_PCT * ENTRY * WIDE_MULT
    assert d1.state.stop_price == lock_price  # the lock floor wins, not the trail

    retrace = lock_price + 0.01  # just above the locked floor
    d2 = evaluate_exit(
        prev=d1.state, side="long", entry_price=ENTRY, last_price=retrace,
        atr_pct=ATR_PCT,
        pnl_r=(retrace - ENTRY) / (ENTRY * ATR_PCT * WIDE_MULT),
        held_seconds=40, mfe_protect=_sched(), stop_atr_mult=WIDE_MULT,
    )
    assert d2.close is False  # held above the locked floor — ladder is ALIVE


def test_default_2x_mult_byte_identical_ladder_still_dominates_trail() -> None:
    # SSOT stop_atr_mult=2.0: every rung was ALREADY inside the trail — the
    # widening is a no-op. Pins byte-identical behaviour for every existing
    # caller (the default trail_mult of 2.0 ATR).
    peak = ENTRY + 0.45 * ATR_PCT * ENTRY * 2.0
    d1 = evaluate_exit(
        prev=_fresh(), side="long", entry_price=ENTRY, last_price=peak,
        atr_pct=ATR_PCT, pnl_r=0.45, held_seconds=20,
        mfe_protect=_sched(), stop_atr_mult=2.0,
    )
    lock_price = ENTRY + 0.20 * ATR_PCT * ENTRY * 2.0
    assert d1.state.stop_price == lock_price  # unchanged: lock floor dominates
    retrace = lock_price + 0.01
    d2 = evaluate_exit(
        prev=d1.state, side="long", entry_price=ENTRY, last_price=retrace,
        atr_pct=ATR_PCT,
        pnl_r=(retrace - ENTRY) / (ENTRY * ATR_PCT * 2.0),
        held_seconds=40, mfe_protect=_sched(), stop_atr_mult=2.0,
    )
    assert d2.close is False  # still held above the (unchanged) locked floor


def test_weekend_maker_3x_mult_ladder_still_inside_trail_byte_identical() -> None:
    # The live-registered weekend-maker mult (3.0): 0.46*3=1.38 ATR < 2.0-ATR
    # trail — already inside, the widening is a no-op, byte-identical to pre-fix.
    peak = ENTRY + 0.46 * ATR_PCT * ENTRY * 3.0
    d = evaluate_exit(
        prev=_fresh(), side="long", entry_price=ENTRY, last_price=peak,
        atr_pct=ATR_PCT, pnl_r=0.46, held_seconds=20,
        mfe_protect=_sched(), stop_atr_mult=3.0,
    )
    lock_price = ENTRY + 0.20 * ATR_PCT * ENTRY * 3.0
    assert d.state.stop_price == lock_price


def test_no_schedule_byte_identical_trail_untouched() -> None:
    # mfe_protect=None (every caller without a schedule, e.g. the tick engine's
    # plain trail-only positions) — the trail basis widening never applies.
    d = evaluate_exit(
        prev=_fresh(), side="long", entry_price=ENTRY, last_price=103.0,
        atr_pct=ATR_PCT, pnl_r=1.5, held_seconds=10, stop_atr_mult=WIDE_MULT,
    )
    assert d.state.stop_price == 103.0 - EXIT_ATR_TRAIL_MULT * 1.0


def test_harvest_state_reversion_bucket_lock_never_exceeds_trail_at_wide_mult() -> None:
    # Root-cause regression (2026-07-02 fresh-fixer audit): the FIRST widen (step
    # 4) divided by ``run_trail_mult`` (the PRE-harvest mult), but once
    # ``new_state == EXIT_STATE_HARVEST`` for a REVERSION-bucket schedule
    # (``peak_lock_arm_r=0.0`` — no peak-lock arm), the trail mult that is
    # ACTUALLY applied (``effective_trail_mult``) tightens to
    # ``EXIT_HARVEST_TRAIL_MULT`` (1.0, strictly tighter than the 2.0-3.0 SSOT /
    # weekend-maker ``run_trail_mult``). Widening against the wrong (wider)
    # divisor under-widens ``atr_one``, so the lock floor's price distance can
    # exceed the trail's OWN (now-tighter) distance — the exact "ladder goes
    # inert" failure mode reopening inside HARVEST. Fixed by dividing by
    # ``effective_trail_mult`` (the mult that is actually applied below).
    mfe_r = EXIT_FSM_HARVEST_R + 0.01  # past HARVEST threshold (float-safe margin).
    peak = ENTRY + mfe_r * ATR_PCT * ENTRY * WIDE_MULT
    d = evaluate_exit(
        prev=_fresh(), side="long", entry_price=ENTRY, last_price=peak,
        atr_pct=ATR_PCT, pnl_r=mfe_r, held_seconds=20,
        mfe_protect=_reversion_sched(), stop_atr_mult=WIDE_MULT,
    )
    assert d.state.exit_state == EXIT_STATE_HARVEST
    lock_dist = 0.20 * ATR_PCT * ENTRY * WIDE_MULT
    assert d.state.stop_price is not None
    stop_dist = peak - d.state.stop_price
    assert stop_dist >= lock_dist - 1e-9


def test_short_side_bep_arm_never_exceeds_trail_symmetric() -> None:
    # Mirror of the long case — short side offsets subtract from entry.
    trough = ENTRY - 0.30 * ATR_PCT * ENTRY * WIDE_MULT
    d = evaluate_exit(
        prev=_fresh("short"), side="short", entry_price=ENTRY, last_price=trough,
        atr_pct=ATR_PCT, pnl_r=0.30, held_seconds=20,
        mfe_protect=_sched(), stop_atr_mult=WIDE_MULT,
    )
    bep_arm_dist = 0.30 * ATR_PCT * ENTRY * WIDE_MULT
    assert d.state.stop_price is not None
    assert d.state.stop_price - trough <= bep_arm_dist + 1e-9
