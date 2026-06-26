"""Tick MFE-protect / scalp / trail / decay harvest helpers (extracted, move-only).

Split out of ``_production_tick_engine`` to keep each module ≤500 LOC. PURE
functions + the tick signal-id constants + the harvest tuning constants — no DB,
no I/O (env reads only). Holds the flow_pressure fix surface the orchestrator
threads into the exit pass: the per-strategy MFE-protect rung resolver
(``_mfe_protect_base_rungs`` / ``_BURST_RIDER_MFE_RUNGS`` / ``_mfe_protect_schedule``),
the OFI decay-gate exit (``_flow_decay_exit``), the let-winners-run trail override
(``_momentum_trail_mult``) and the reversion scalp exit (``_scalp_exit_decision``).
Re-exported by ``_production_tick_engine`` so every existing import keeps working
byte-for-byte.
"""

from __future__ import annotations

import os

from polaris.core.economics.fees import real_fee_bps
from polaris.core.live_recalc.exit_engine import MfeProtectSchedule
from polaris.core.regime_fit import exit_tightness, regime_fit
from polaris.core.ticks.config import TickEngineConfig


def _env_pos_float(name: str, default: float) -> float:
    """Positive-float env override (``default`` for unset / non-positive / bad)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0.0 else default


# Tick signal_id of the order-flow-imbalance momentum signal (also the
# strategy_id stamped on the positions it opens) — the retune target.
_FLOW_PRESSURE = "flow_pressure"
_MICRO_REVERSION = "micro_reversion"
_BURST_RIDER = "burst_rider"

# A reversion (fast-scalp) position closes on the FIRST of: flow reversal (the
# book turned against the fade), a micro-stop (adverse R past this floor), or a
# small-R target (the snap-back banked). EXPECTANCY, not a throttle — it cuts a
# dead scalp fast and banks a small winner fast; it never reduces size or blocks
# an entry. R units share ``compute_unrealized_pnl_r``'s ATR-R basis.
_SCALP_TARGET_R = 0.5
_SCALP_STOP_R = -0.4

# #37 micro_reversion 0-second-hold fix ([[micro_reversion_0sec_hold_2026-06-26]]).
# The ``flow_reversal`` scalp branch fired the instant the book imbalance ``ofi``
# MARGINALLY opposed the side (a ZERO deadband). But a freshly-faded overshoot's
# book is NORMALLY still marginally opposed at entry, so the exit fired at 0.0s —
# every close a -0.001R micro loss eaten by spread+fee (31% of OKX closes <=2s on a
# move smaller than the spread). The fix is flow_not_block (DEFER the marginal-flow
# exit to a meaningful capture, NOT a block / size-cut / wider rail):
#   - ``_SCALP_FLOW_REVERSAL_OFI`` ([-1,1] book-imbalance magnitude): a GENUINE
#     break — ``|ofi|`` FIRMLY reversed past this floor — still exits IMMEDIATELY
#     (misclassification CORRECTION, not deferring a real break). Set on the SAME
#     [-1,1] scale as the entry ``theta_ofi`` (0.25 default ~ a clearly bid/ask-heavy
#     ~62/38 book), so a firmly-reversed book is a real reversal while the marginal
#     post-fade baseline is not. Env-tunable.
#   - A MARGINAL opposing ``ofi`` (below the floor) only exits once the capture has
#     cleared the spread+fee cost band (``min_capture_r`` supplied by the caller);
#     below cost it HOLDS, riding to ``scalp_target`` (a meaningful capture) or the
#     UNCHANGED ``scalp_stop`` -0.4R rail. ASYMMETRY preserved (loss side untouched).
_SCALP_FLOW_REVERSAL_OFI: float = _env_pos_float(
    "POLARIS_TICK_SCALP_FLOW_REVERSAL_OFI", 0.25
)

# The R-unit ruler the scalp pnl_r shares (compute_unrealized_pnl_r's stop_atr_mult
# default — 1R == entry x atr_pct x this). Referenced so the spread+fee -> R cost
# floor below uses the SAME denominator the pnl_r it gates is measured on (no magic
# 2.0). Env-tunable for parity if the shared stop multiple is ever re-aimed.
_SCALP_STOP_ATR_MULT: float = _env_pos_float("POLARIS_TICK_SCALP_STOP_ATR_MULT", 2.0)

# Per-strategy reversion TAKE-PROFIT override (R) — PROFIT SIDE ONLY.
# [[harvest_generalization_2026-06-23]]: micro_reversion is the top harvest target
# (avg MFE +0.523R over 120 trades, realized only -0.148R). Its bounded
# revert-to-mean reached +0.30R for 57% and +0.45R for 44% of trades, yet the
# shared +0.50R scalp target sat ABOVE that mass — so the revert peaked and gave
# back before banking. A LOWER per-strategy target harvests the measured revert
# (BB-fade enters at the band extreme, targets the middle — a bounded gain, NOT
# let-winners-run). This ONLY moves the take-profit DOWN (banks sooner); the loss
# side (``_SCALP_STOP_R``) and the flow-reversal exit are UNTOUCHED — NOT a tighter
# loss cut / defensive throttle. Env-tunable; an unmapped reversion id keeps the
# shared ``_SCALP_TARGET_R`` default (byte-identical).
_MICRO_REVERSION_TARGET_R: float = _env_pos_float(
    "POLARIS_TICK_MICRO_REVERSION_TARGET_R", 0.35
)


def _scalp_target_for_strategy(strategy_id: str) -> float:
    """Reversion take-profit (R) for ``strategy_id`` — PROFIT side only.

    micro_reversion harvests its measured bounded revert at the lower
    ``_MICRO_REVERSION_TARGET_R``; every other reversion id keeps the shared
    ``_SCALP_TARGET_R`` default (byte-identical). Never touches the loss side.
    """
    if strategy_id == _MICRO_REVERSION:
        return _MICRO_REVERSION_TARGET_R
    return _SCALP_TARGET_R


def _scalp_min_capture_r(
    *, venue: str, spread_bps: float | None, entry_atr_pct: float | None
) -> float | None:
    """Spread+fee round-trip cost as a fraction of the scalp R-ruler, or ``None``.

    #37 fix: the meaningful-capture floor below which a MARGINAL flow turn must not
    bank a reversion (it would only book the round-trip cost = a 0s micro loss). The
    cost is ``spread_bps + 2 x real_fee_bps(venue)`` (one spread + a real taker fee
    on each of the two legs), as a fraction of notional, divided by the SAME R-unit
    price-fraction the scalp ``pnl_r`` is measured on (``entry_atr_pct x
    _SCALP_STOP_ATR_MULT``) so the floor and the pnl_r it gates share one ruler.

    Uses the REAL fee schedule (the documented go-live edge basis — OKX 10bps), NOT
    the 70bps OKX demo billing artifact: a demo-fee floor would be ~0.7%+ and
    effectively block every reversion exit (a defensive throttle). Returns ``None``
    on a missing / non-positive ATR anchor or spread (capture path disabled →
    firm-break-only), legacy-graceful, never a divide-by-zero. PURE.
    """
    if entry_atr_pct is None or entry_atr_pct <= 0.0:
        return None
    if spread_bps is None or spread_bps < 0.0:
        return None
    rt_cost_bps = spread_bps + 2.0 * real_fee_bps(venue)
    cost_frac = rt_cost_bps / 10_000.0
    r_unit_frac = entry_atr_pct * _SCALP_STOP_ATR_MULT
    if r_unit_frac <= 0.0:
        return None
    return cost_frac / r_unit_frac


# Per-strategy momentum exit trail width (ATR units) override for the precise
# exit, by tick signal_id. flow_pressure's favourable OFI drift was being closed
# too fast by the default 2.0-ATR Chandelier trail (honest fee-net retune
# w02ccvq0q: the longer-hold cohort was the only gross-cost-positive bucket), so
# it runs on a WIDER trail to capture the drift past the old ~75s scalp.
# burst_rider runs on its OWN wide trail (3.5) so its measured +7.33R spike is not
# flat-lined by the default 2-ATR trail ([[ab_letrun_maker_2026-06-24]]) — the
# peak-fraction floor (below) supplies the lock above the +1.0R arm.
# EXPECTANCY, not a throttle: it only LOOSENS the running trail (lets the winner
# run); the ratchet, protected-BEP, loser-timeout and the G6 -1.0R hard rail (the
# loss-defence) are all untouched, and HARVEST still tightens. Env-tunable so each
# can be re-aimed after the honest-slate re-measure. An unlisted signal_id → None
# → module default.
_FLOW_PRESSURE_TRAIL_MULT_DEFAULT = 4.0
_BURST_RIDER_TRAIL_MULT_DEFAULT = 3.5

# Let-winners-run peak-fraction arm/frac for the tick MOMENTUM family
# ([[ab_letrun_maker_2026-06-24]] · #47 recalib [[exit_recalib_2026-06-26]]).
# Red-team calibration: arm at +0.30R — the COMMON-CASE rung. Post-reset
# 2026-06-25 ledger: 32.9% of trades reach +0.30R but only 18.4% reach +0.45R, so
# the old +0.45R arm STILL starved a third of the common case — a +0.30-0.45R peak
# round-tripped on the wide trail with NOTHING locked. +0.30R arms the EARLIEST
# common peak and is FEE-SAFE (frac 0.50 → lock >= +0.15R at the arm, above the OKX
# spread+fee band). frac 0.50 UNCHANGED. burst_rider AND flow_pressure both ride it
# (consistency; the floor only ratchets the stop toward profit — never a throttle).
# The sub-arm fixed rungs (burst 0.35/0.50/0.25, flow_pressure the cfg band) stay
# the BELOW-arm phase. Env-tunable.
_TICK_PEAK_LOCK_ARM_R: float = _env_pos_float("POLARIS_TICK_PEAK_LOCK_ARM_R", 0.30)
_TICK_PEAK_LOCK_FRAC: float = _env_pos_float("POLARIS_TICK_PEAK_LOCK_FRAC", 0.50)

# Reversion scalp peak GIVE-BACK arm/frac (#19, post-reset 2026-06-25 ledger).
# micro_reversion is 63% of closes (48/76) and is REVERSION family → it exits via
# ``_scalp_exit_decision`` ONLY (scalp_target / scalp_stop / flow_reversal), with
# NO peak protection between entry and the 0.35R target. 32.9% of trades reach
# +0.30R but only 18.4% reach the 0.35R bank — a +0.30R reversion peak that
# reverses gave it ALL back to scalp_stop (-0.4R) / flow_reversal (~0). This adds
# a PROFIT-SIDE give-back catch: once the reversion peaked past ``_SCALP_PEAK_ARM_R``
# (0.25R — just below the 0.35R target, so it only catches a peak that nearly hit
# the bank) and the live pnl_r surrenders below ``peak_r * _scalp_peak_frac()`` while
# STILL POSITIVE, it banks ``scalp_giveback`` instead of round-tripping. frac 0.60
# (reversion is a bounded revert — a slightly looser give-back than the momentum
# 0.50 so it banks a meaningful fraction, not a crumb). FEE-SAFE FLOOR: the
# give-back fires ONLY if the surviving pnl_r still clears ``_SCALP_PEAK_MIN_BANK_R``
# (0.10R) — a peak that just armed at 0.25R then snapped to ~0 must NOT bank a
# fee-negative crumb (it rides to scalp_stop / flow_reversal instead). ASYMMETRY:
# the loss side (``_SCALP_STOP_R`` -0.4R) is UNTOUCHED — the give-back NEVER fires
# on a negative pnl (positive-only). Env-tunable; ``peak_r`` omitted (legacy /
# momentum) disarms it (byte-identical).
#
# #47 ([[exit_recalib_2026-06-26]]): the give-back FRAC is read FRESH per call from
# ``POLARIS_SCALP_PEAK_FRAC`` (default 0.60) — NOT cached at import — so the live
# A/B can inject 0.75 (banks a larger fraction sooner) on the RUNNING bot via env
# without a restart. ``_SCALP_PEAK_ARM_R`` 0.25 UNCHANGED. A tighter frac never
# touches the loss side (still profit-side-only above the fee-safe floor).
_SCALP_PEAK_ARM_R: float = _env_pos_float("POLARIS_TICK_SCALP_PEAK_ARM_R", 0.25)
_SCALP_PEAK_MIN_BANK_R: float = _env_pos_float("POLARIS_TICK_SCALP_PEAK_MIN_BANK_R", 0.10)


def _scalp_peak_frac() -> float:
    """Reversion peak give-back fraction, READ FRESH (no import-time cache).

    ``POLARIS_SCALP_PEAK_FRAC`` (default 0.60). Read per scalp-exit call so the live
    A/B can inject 0.75 on the running bot via env without a restart. The live
    ``pnl_r`` surrendering below ``peak_r * this`` (while above the fee-safe floor)
    banks ``scalp_giveback``; a tighter frac only banks a larger fraction SOONER —
    PROFIT-side only, the -0.4R loss rail is untouched.
    """
    return _env_pos_float("POLARIS_SCALP_PEAK_FRAC", 0.60)


def _momentum_trail_mult(strategy_id: str) -> float | None:
    """Let-winners-run ATR-trail override (ATR units) for a tick momentum strategy.

    flow_pressure (4.0) and burst_rider (3.5) each run on a WIDER trail so a
    favourable run is captured past the old fast scalp; every other tick strategy
    returns ``None`` → the module-default trail (byte-identical). Env-overridable
    per strategy.
    """
    if strategy_id == _FLOW_PRESSURE:
        return _env_pos_float(
            "POLARIS_TICK_FLOW_PRESSURE_TRAIL_MULT", _FLOW_PRESSURE_TRAIL_MULT_DEFAULT
        )
    if strategy_id == _BURST_RIDER:
        return _env_pos_float(
            "POLARIS_TICK_BURST_RIDER_TRAIL_MULT", _BURST_RIDER_TRAIL_MULT_DEFAULT
        )
    return None


# Tick MOMENTUM signals routed through ``run_precise_exit`` — BOTH now carry the
# MFE-protect harvest schedule. [[harvest_generalization_2026-06-23]]: the harvest
# was wired ONLY to flow_pressure; burst_rider (also momentum) passed
# mfe_protect=None, so its measured +0.3R excursions (27% reach +0.30R)
# round-tripped on the wide ATR trail. micro_reversion is REVERSION family (scalp
# exit, not run_precise_exit) → it is NOT here; it harvests via its scalp target.
# Per-strategy MFE-protect BASE rungs (bep_at_r, protect_at_r, lock_r) BEFORE the
# Seam3 regime-fit transform. Each momentum signal is calibrated to its OWN
# excursion mass, so the rungs are strategy-SCOPED — a re-aim of one MUST NOT
# bleed onto the other.
#   - flow_pressure: reads the cfg fields (the env-tunable SSOT). RE-AIMED to the
#     measured flow_pressure +MFE band 0.20/0.30/0.15 ([[flow_pressure_
#     continuation_gate_2026-06-24]]).
#   - burst_rider: its ORIGINAL rungs 0.35/0.50/0.25, calibrated to burst_rider's
#     own measured +0.3R excursion mass ([[harvest_generalization_2026-06-23]]);
#     pinned here so the flow_pressure re-aim leaves it byte-identical.
_BURST_RIDER_MFE_RUNGS: tuple[float, float, float] = (0.35, 0.50, 0.25)


def _mfe_protect_base_rungs(
    strategy_id: str, cfg: TickEngineConfig
) -> tuple[float, float, float] | None:
    """Per-strategy base MFE-protect rungs (bep_at_r, protect_at_r, lock_r), or None.

    flow_pressure → the env-tunable ``cfg`` SSOT; burst_rider → its own pinned
    original rungs. An unmapped id → ``None`` (no schedule). Scoped so a re-aim of
    one strategy's rungs leaves the other byte-identical.
    """
    if strategy_id == _FLOW_PRESSURE:
        return (cfg.mfe_bep_r, cfg.mfe_protect_r, cfg.mfe_protect_lock_r)
    if strategy_id == _BURST_RIDER:
        return _BURST_RIDER_MFE_RUNGS
    return None


def _mfe_protect_schedule(
    strategy_id: str, cfg: TickEngineConfig, *, regime: str | None = None
) -> MfeProtectSchedule | None:
    """MFE-protect harvest schedule for a tick MOMENTUM strategy, or ``None``.

    Both momentum tick signals (flow_pressure + burst_rider, routed through
    ``run_precise_exit``) ratchet the stop to BEP and lock positive R at their
    OWN per-strategy rungs (``_mfe_protect_base_rungs``) — capturing the measured
    +MFE excursion and cutting the give-back. The rungs are strategy-SCOPED:
    flow_pressure reads the cfg SSOT (re-aimed to its measured band), burst_rider
    keeps its own original 0.35/0.50/0.25 (calibrated to ITS excursion mass), so a
    re-aim of one leaves the other byte-identical. burst_rider was previously
    uncovered (→ None) so its +0.3R excursions round-tripped on the wide ATR trail;
    it now has its own schedule. micro_reversion (reversion family → scalp exit) →
    ``None`` here (it harvests via its scalp profit target). An unmapped id →
    ``None`` → byte-identical module-default exit. EXPECTANCY, not a throttle: the
    schedule only tightens the stop toward profit.

    Seam3 (regime-fit): a BAD momentum fit (chop churn = -1) TIGHTENS the harvest
    — the BEP/protect thresholds are pulled IN (divided by ``exit_tightness > 1``)
    so a mis-regime trade banks its excursion sooner; a GOOD fit LOOSENS them
    (LET_RUN). This only ratchets the protective stop TOWARD profit — it never
    reduces size, never blocks/vetoes entry, never loosens below the G6 -1.0R rail
    (run_precise_exit takes the protection-tighter of trail vs floor). The lock_r
    floor is preserved (a tightened schedule never locks LESS positive R).
    """
    rungs = _mfe_protect_base_rungs(strategy_id, cfg)
    if rungs is None:
        return None
    bep_r, protect_r, lock_r = rungs
    # Momentum family (only momentum signals reach here). exit_tightness > 1 on a
    # bad fit, < 1 on a good fit, 1.0 neutral / unknown regime → byte-identical.
    t = exit_tightness(regime_fit("momentum", regime))
    return MfeProtectSchedule(
        bep_at_r=bep_r / t,
        protect_at_r=protect_r / t,
        # Lock AT LEAST the configured positive R — a tighter schedule never banks
        # LESS (ratchet-toward-profit invariant); a looser fit keeps the base lock.
        lock_r=lock_r * max(1.0, t),
        # Let-winners-run peak-fraction floor: above the +1.0R arm the stop holds
        # ~50% of the REACHED peak MFE so a confirmed big winner (burst_rider's
        # +7.33R) is locked at peak% instead of the small fixed lock that flat-lined
        # it ([[ab_letrun_maker_2026-06-24]]). REGIME-INDEPENDENT (the wide trail +
        # the let-run lock are the aggressive let-winners-run, not a regime tighten),
        # so the Seam3 transform above does NOT touch the arm/frac.
        peak_lock_arm_r=_TICK_PEAK_LOCK_ARM_R,
        peak_lock_frac=_TICK_PEAK_LOCK_FRAC,
    )


def _flow_decay_exit(
    *, side: str, ofi: float | None, flow_confirmed: bool | None, pnl_r: float,
    mfe_gate_r: float,
) -> bool:
    """True iff a GREEN flow_pressure position should exit on OFI decay / failure.

    Once favourable excursion has appeared (``pnl_r >= mfe_gate_r``), trail on the
    MICROSTRUCTURE — not just price distance — so the +0.67R MFE is banked near the
    peak instead of round-tripping the wide ATR trail (the 17% give-back). Exits
    when the order-flow that drove the entry DECAYS or REVERSES against the side:
    the book imbalance flips against the position (``ofi`` sign opposes) OR the
    post-spike follow-through has FAILED (``flow_confirmed`` is False — bid
    withdrawal / microprice failure / spread widening). EXPECTANCY, not a throttle:
    a per-position close once green; never a size-cut, an entry-block, or a halt —
    and it never fires while the trade is red (the G6 -1.0R rail owns that).
    """
    if pnl_r < mfe_gate_r:
        return False
    if ofi is not None:
        if side == "long" and ofi < 0.0:
            return True
        if side == "short" and ofi > 0.0:
            return True
    return flow_confirmed is False


def _scalp_exit_decision(
    *,
    side: str,
    entry_price: float,
    last_mid: float,
    ofi: float | None,
    pnl_r: float,
    strategy_id: str = "",
    peak_r: float | None = None,
    min_capture_r: float | None = None,
) -> str | None:
    """Fast-scalp exit for a reversion position — close-reason or None (hold).

    Closes on the FIRST of:
      - ``scalp_stop``: adverse excursion past ``_SCALP_STOP_R`` (micro-stop).
      - ``scalp_target``: the snap-back banked the strategy's take-profit (small
        R). ``strategy_id`` selects a per-strategy PROFIT target — micro_reversion
        harvests its measured bounded revert at the lower
        ``_MICRO_REVERSION_TARGET_R`` so the +0.30-0.45R excursion is banked
        before it gives back; every other reversion id keeps the shared
        ``_SCALP_TARGET_R`` (byte-identical). PROFIT side only.
      - ``scalp_giveback`` (#19 peak give-back): once the reversion has PEAKED past
        ``_SCALP_PEAK_ARM_R`` (``peak_r``, the running max favourable R) and the
        live ``pnl_r`` has surrendered below ``peak_r * _scalp_peak_frac()`` while
        STILL ABOVE the fee-safe floor ``_SCALP_PEAK_MIN_BANK_R``, bank the locked
        fraction NOW instead of round-tripping to scalp_stop / flow_reversal (the
        32.9%-reach-+0.30R-but-only-18.4%-bank give-back). ``peak_r is None``
        (legacy / momentum callers) DISARMS it (byte-identical). PROFIT side only —
        it NEVER fires on a negative pnl nor banks a fee-negative crumb.
      - ``flow_reversal``: order-flow imbalance turned against the fade. #37 fix:
        only a GENUINE break exits at 0s — ``|ofi|`` FIRMLY reversed past
        ``_SCALP_FLOW_REVERSAL_OFI`` (the misclassification CORRECTION: a real big
        adverse book is exempt from the hold). A MARGINAL opposing ``ofi`` (below
        that floor) is the baseline post-fade book state, NOT a reversal — it only
        exits once the capture has cleared the spread+fee cost band
        (``min_capture_r``); below cost it HOLDS, riding to ``scalp_target`` or the
        UNCHANGED ``scalp_stop``. ``min_capture_r is None`` (legacy / momentum) →
        the capture path is disabled (firm-break-only). This kills the 0.0s
        -0.001R micro-loss churn (flow_not_block — a DEFERRED marginal-flow exit,
        never a block / size-cut / wider rail).
    EXPECTANCY (cut a dead scalp fast / bank a small winner fast), NOT a throttle.
    The loss side (``_SCALP_STOP_R``) is UNCHANGED regardless of strategy_id /
    peak_r / min_capture_r — asymmetry preserved (every PROFIT-side change above
    only ever DEFERS a banking; none touches the -0.4R rail).
    """
    if pnl_r <= _SCALP_STOP_R:
        return "scalp_stop"
    if pnl_r >= _scalp_target_for_strategy(strategy_id):
        return "scalp_target"
    # Peak give-back (PROFIT side only): a reversion that reached an armed peak and
    # is now surrendering it (still above the fee-safe floor) banks the locked
    # fraction instead of giving the whole peak back. ``peak_r is None`` → disarmed
    # (byte-identical). The ``pnl_r >= _SCALP_PEAK_MIN_BANK_R`` floor stops it from
    # banking a fee-negative crumb when a peak barely armed then snapped to ~0.
    if (
        peak_r is not None
        and peak_r >= _SCALP_PEAK_ARM_R
        and _SCALP_PEAK_MIN_BANK_R <= pnl_r < peak_r * _scalp_peak_frac()
    ):
        return "scalp_giveback"
    if ofi is not None:
        # A long reversion bet wants flow to stop selling; a short wants it to stop
        # buying. The opposing-book magnitude decides whether this is a GENUINE
        # break or just the marginal post-fade baseline (#37).
        opposing = (side == "long" and ofi < 0.0) or (side == "short" and ofi > 0.0)
        if opposing:
            # GENUINE break → exit NOW even at ~0 capture (real adverse book,
            # exempt from the hold).
            if abs(ofi) >= _SCALP_FLOW_REVERSAL_OFI:
                return "flow_reversal"
            # MARGINAL opposing flow → only bank once the capture cleared spread+fee
            # (a meaningful capture, not a 0s micro loss). Below cost / no floor →
            # HOLD (rides to scalp_target or the unchanged scalp_stop).
            if min_capture_r is not None and pnl_r >= min_capture_r:
                return "flow_reversal"
    return None
