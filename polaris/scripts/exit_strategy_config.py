"""Per-strategy let-winners-run exit config — split out of ``_production_recalc_exit``.

Move-only extraction for the ≤500-LOC budget: the env helpers, the BAR TREND
peak-fraction / trail constants, and the per-strategy MFE-protect / trail-width /
exit-archetype lookups. ``_production_recalc_exit`` re-exports these so existing
import paths (and ``run_precise_exit`` / ``_assess_mode_for_position`` global
lookups) keep working byte-identically.

EXPECTANCY, not a defensive throttle: every helper here only ratchets the stop
toward profit or widens a winner's trail — size / entry side / the G6 -1.0R rail
are untouched.
"""

from __future__ import annotations

import os

from polaris.core.live_recalc.exit_engine import (
    EXIT_BAR_MFE_BEP_R,
    EXIT_BAR_MFE_LOCK_R,
    EXIT_BAR_MFE_PROTECT_R,
    EXIT_EQUITY_MFE_BEP_R,
    EXIT_EQUITY_MFE_LOCK_R,
    EXIT_EQUITY_MFE_PROTECT_R,
    Bucket,
    MfeProtectSchedule,
    bucket_from_correlation_group,
)
from polaris.strategies import STRATEGY_REGISTRY


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0.0 else default


# Let-winners-run peak-fraction floor for the BAR TREND family
# ([[ab_letrun_maker_2026-06-24]] · arm 0.30→0.20 / frac 0.55→0.65
# [[session_giveback_bar_arm]]). The bar TREND strategies (session_breakout /
# fx_breakout_basket / xau_indices_trend / spot_donchian — every TREND-bucket
# registered strategy) have entry edge (positive MFE) but reap almost none of it:
# the biggest live leak is atr_trail_stop closes (n=20) where big winners
# round-trip on the wide 3.0-ATR trail. The peak-fraction floor holds ``frac`` of
# the reached peak once MFE passes the arm (it IS wired and DOES fire —
# volume_burst locked_R = peak×frac confirmed live).
#   ARM 0.30 → 0.20: the live MFE distribution (data/polaris_live.sqlite,
#   position_strategy_segments.exit_reason join) reaches +0.20R for 27.9% of
#   closes, +0.30R for 20%, +0.45R for 10.6% — so the COMMON +0.20R winner armed
#   NOTHING at the old +0.30R arm and round-tripped to a shallow exit
#   (loser_timeout / exit). +0.20R arms the floor on that common winner.
#   FRAC 0.55 → 0.65: at 0.55 a big winner always EXPOSED 45% of the reached
#   peak. 0.65 shrinks that give-back to 35%. The floor is a peak-TRACKING ratchet
#   (``entry ± peak_mfe_r * frac * atr_r`` climbs with the running max-MFE), so a
#   higher fraction is NOT an early cut — it never caps the upside, only lifts the
#   protective stop closer to the reached peak (asymmetry preserved).
#   TRAIL 3.0 UNCHANGED: narrowing it would clip the FREQUENT winner early (a
#   loss); the lock is supplied by the peak-fraction floor, not the trail (the
#   trail stays wide on the WIDENED 3.0-ATR width, was the 2.0 module default).
#   REVERSION / range bucket is UNCHANGED (arm 0.0 → disabled; their edge is a
#   bounded revert-to-mean, never a let-winners-run). EXPECTANCY, not a throttle:
#   it only ratchets the stop toward profit; size / entry side / the G6 -1.0R rail
#   are untouched. Env-tunable.
BAR_TREND_PEAK_LOCK_ARM_R: float = _env_float("POLARIS_BAR_TREND_PEAK_LOCK_ARM_R", 0.20)
BAR_TREND_PEAK_LOCK_FRAC: float = _env_float("POLARIS_BAR_TREND_PEAK_LOCK_FRAC", 0.65)
BAR_TREND_TRAIL_MULT: float = _env_float("POLARIS_BAR_TREND_TRAIL_MULT", 3.0)


def _mfe_protect_for_strategy(strategy_id: str) -> MfeProtectSchedule | None:
    """MFE-protect harvest schedule for a REGISTERED bar strategy, or ``None``.

    [[harvest_generalization_2026-06-23]]: the harvest floor started equity-ONLY
    (asset_class=='equity'); the 9 other bar strategies passed
    ``run_precise_exit`` NO schedule, so below EXIT_FSM_PROTECT_R=1.0R the only
    floor was the wide 2-ATR trail. MEASURED on the live ledger: 93.5% of trades
    never reach +1.0R MFE (the dead PROTECT rung), yet 29.2% reach +0.30R and
    19.7% reach +0.45R — those excursions round-tripped (avg MFE +0.278R,
    realized -0.947R, giveback 1.225R). This now routes EVERY registered bar
    strategy — every ``asset_class`` (spot / fx / index / commodity / equity) —
    to a calibrated schedule so the +0.30R / +0.45R excursions become
    break-even-or-better exits:

      * equity keeps its OWN proven, separately-named rungs (byte-identical to
        the prior equity-only behaviour — the 0%-win 1D round-trips it fixed);
      * every other asset_class uses the shared bar default (the SAME proven
        +0.30R BEP / +0.45R protect / +0.20R lock, calibrated to the aggregate
        measured distribution — 29.2% reach +0.30R, 19.7% reach +0.45R).

    EXPECTANCY, not a throttle: it ONLY ratchets the stop toward profit (the
    let-winners-run ATR trail still runs ABOVE the floor; size / entry side / the
    G6 -1.0R rail are untouched). Tick-engine ids (flow_pressure / micro_reversion
    / burst_rider) are NOT registered here — they carry their OWN tick-path
    schedule — so an unregistered id → ``None`` → byte-identical pre-fix exit.
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return None
    # Let-winners-run peak-fraction floor — TREND bucket only. A TREND-bucket bar
    # strategy (session_breakout / fx_breakout_basket / xau_indices_trend /
    # spot_donchian) arms the peak-fraction floor at +0.20R / frac 0.65; the
    # REVERSION / range bucket leaves it DISABLED (arm 0.0 → byte-identical)
    # because its edge is a bounded revert-to-mean, not a winner to let run. The
    # fixed sub-arm rungs below stay the BELOW-arm phase for every bucket
    # ([[ab_letrun_maker_2026-06-24]]).
    is_trend = (
        bucket_from_correlation_group(cls.metadata.correlation_group_id)
        is Bucket.TREND
    )
    peak_arm = BAR_TREND_PEAK_LOCK_ARM_R if is_trend else 0.0
    peak_frac = BAR_TREND_PEAK_LOCK_FRAC if is_trend else 0.0
    if cls.metadata.asset_class == "equity":
        return MfeProtectSchedule(
            bep_at_r=EXIT_EQUITY_MFE_BEP_R,
            protect_at_r=EXIT_EQUITY_MFE_PROTECT_R,
            lock_r=EXIT_EQUITY_MFE_LOCK_R,
            peak_lock_arm_r=peak_arm,
            peak_lock_frac=peak_frac,
        )
    return MfeProtectSchedule(
        bep_at_r=EXIT_BAR_MFE_BEP_R,
        protect_at_r=EXIT_BAR_MFE_PROTECT_R,
        lock_r=EXIT_BAR_MFE_LOCK_R,
        peak_lock_arm_r=peak_arm,
        peak_lock_frac=peak_frac,
    )


def _trail_mult_for_strategy(strategy_id: str) -> float | None:
    """Let-winners-run ATR-trail width (ATR units) for a REGISTERED bar strategy.

    A TREND-bucket bar strategy widens to ``BAR_TREND_TRAIL_MULT`` (3.0, was the
    2.0 module default) so a confirmed winner runs and the peak-fraction floor (not
    a 2-ATR trail) supplies the lock ([[ab_letrun_maker_2026-06-24]]). REVERSION /
    range bucket and unregistered ids → ``None`` → module default (byte-identical).
    Only LOOSENS the running trail (lets the winner run); the ratchet / floor /
    loser-timeout / G6 -1.0R rail are untouched. Tick-engine ids carry their OWN
    trail and never reach here.
    """
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return None
    if bucket_from_correlation_group(cls.metadata.correlation_group_id) is Bucket.TREND:
        return BAR_TREND_TRAIL_MULT
    return None


def _bucket_for_strategy(strategy_id: str) -> Bucket:
    """Exit archetype (TREND / REVERSION) for ``strategy_id`` from its registered
    ``correlation_group_id``. Unregistered / unknown → TREND (let-winners-run
    default — flow_not_block: never force an unmapped strategy tighter)."""
    cls = STRATEGY_REGISTRY.get(strategy_id)
    if cls is None:
        return Bucket.TREND
    return bucket_from_correlation_group(cls.metadata.correlation_group_id)
