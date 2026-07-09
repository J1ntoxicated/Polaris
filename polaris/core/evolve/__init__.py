"""P0a evolve package — OFFLINE config-variant search over existing strategies.

OFFLINE ONLY. Touches NO live trading / sizing / T4 chain / gate thresholds.
The variant layer only rebinds design-verified, bounded-numeric ENTRY-TRIGGER
(+ exit-timing TTL) knobs via dynamic subclass overrides; default instances are
byte-identical to the frozen baseline (behavior-0).
"""

from __future__ import annotations

from polaris.core.evolve.evaluator import (
    VariantEval,
    build_baselines,
    cross_variant_sr_variance,
    evaluate_variant,
    gate_can_discriminate,
    honest_dsr,
    min_passable_n,
    split_is_oos,
)
from polaris.core.evolve.param_bounds import PARAM_BOUNDS, varyable_params
from polaris.core.evolve.registry import (
    CellAggregate,
    PassRateAggregate,
    SurvivorAdmissionRow,
    TrialRow,
    aggregate_pass_rate,
    count_active_admissions,
    cumulative_trials_per_cell,
    open_registry,
    record_survivor_admission,
    record_trial,
    resolve_survivor_admission,
)
from polaris.core.evolve.survivor_gate import (
    K_MIN_INDEPENDENT_CELLS,
    GuardVerdict,
    SurvivorTrial,
    SurvivorVerdict,
    cohort_cap_guard,
    evaluate_survivor,
    grid_corner_guard,
    independent_cell_guard,
    load_variant_trials,
)
from polaris.core.evolve.variants import (
    GRID_TOTAL_CAP,
    StrategyVariant,
    enumerate_grid,
    make_variant,
)

__all__ = [
    "GRID_TOTAL_CAP",
    "K_MIN_INDEPENDENT_CELLS",
    "PARAM_BOUNDS",
    "CellAggregate",
    "GuardVerdict",
    "PassRateAggregate",
    "StrategyVariant",
    "SurvivorAdmissionRow",
    "SurvivorTrial",
    "SurvivorVerdict",
    "TrialRow",
    "VariantEval",
    "aggregate_pass_rate",
    "build_baselines",
    "cohort_cap_guard",
    "count_active_admissions",
    "cross_variant_sr_variance",
    "cumulative_trials_per_cell",
    "enumerate_grid",
    "evaluate_survivor",
    "evaluate_variant",
    "gate_can_discriminate",
    "grid_corner_guard",
    "honest_dsr",
    "independent_cell_guard",
    "load_variant_trials",
    "make_variant",
    "min_passable_n",
    "open_registry",
    "record_survivor_admission",
    "record_trial",
    "resolve_survivor_admission",
    "split_is_oos",
    "varyable_params",
]
