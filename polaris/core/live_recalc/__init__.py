"""Layer 6 — Live Recalc + Self-Correction registry.

Spec sources:
- vault/30_components/layer-6-live-recalc.md
- vault/10_decisions/ADR-003 §Layer 6

P0 modules (this directory) are STUBS / state machines. Full Universal
3-layer formula + AI position monitor swap evaluation are P1.
"""

from __future__ import annotations

from polaris.core.live_recalc.conviction import (
    CONVICTION_GROUP_CAP_MULT,
    CONVICTION_LAYER_MULTS,
    CONVICTION_MAX_LAYERS,
    CONVICTION_MIN_PNL_R,
    ConvictionDecision,
    ConvictionGateInputs,
    can_stack_conviction,
    compute_stack_size_mult,
    count_layers,
)
from polaris.core.live_recalc.excursion import (
    compute_excursion_r,
    compute_mae_r,
    compute_mfe_r,
)
from polaris.core.live_recalc.regime_flip import (
    REGIME_FLIP_CONFIRM_CLOSES,
    REGIME_VALUES,
    RegimeFlipDecision,
    classify_regime,
    detect_regime_flip,
    fetch_regime,
)
from polaris.core.live_recalc.strategy_swap import (
    SWAP_MAX_PER_TRADE,
    SwapCandidate,
    SwapDecision,
    evaluate_strategy_swap,
)
from polaris.core.live_recalc.tick_recalc import (
    EXIT_DIRTY_PNL_BOUNDARIES_R,
    EXIT_DIRTY_PRICE_ATR_MULT,
    EXIT_OVERRIDE_COOLDOWN_SEC,
    EXIT_OVERRIDE_MAX_PER_TRADE,
    LIVE_RECALC_BATCH_P95_MS,
    LIVE_RECALC_CADENCE_SEC,
    LIVE_RECALC_MAX_POSITIONS,
    RecalcLogEntry,
    mark_position_dirty,
    mark_positions_dirty,
    recompute_exit_params,
    run_live_recalc_cycle,
    should_run_recalc,
)

__all__ = [
    "CONVICTION_GROUP_CAP_MULT",
    "CONVICTION_LAYER_MULTS",
    "CONVICTION_MAX_LAYERS",
    "CONVICTION_MIN_PNL_R",
    "ConvictionDecision",
    "ConvictionGateInputs",
    "EXIT_DIRTY_PNL_BOUNDARIES_R",
    "EXIT_DIRTY_PRICE_ATR_MULT",
    "EXIT_OVERRIDE_COOLDOWN_SEC",
    "EXIT_OVERRIDE_MAX_PER_TRADE",
    "LIVE_RECALC_BATCH_P95_MS",
    "LIVE_RECALC_CADENCE_SEC",
    "LIVE_RECALC_MAX_POSITIONS",
    "REGIME_FLIP_CONFIRM_CLOSES",
    "REGIME_VALUES",
    "RecalcLogEntry",
    "RegimeFlipDecision",
    "SWAP_MAX_PER_TRADE",
    "SwapCandidate",
    "SwapDecision",
    "can_stack_conviction",
    "classify_regime",
    "compute_excursion_r",
    "compute_mae_r",
    "compute_mfe_r",
    "compute_stack_size_mult",
    "count_layers",
    "detect_regime_flip",
    "evaluate_strategy_swap",
    "fetch_regime",
    "mark_position_dirty",
    "mark_positions_dirty",
    "recompute_exit_params",
    "run_live_recalc_cycle",
    "should_run_recalc",
]
