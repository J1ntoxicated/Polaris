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
    TrialRow,
    aggregate_pass_rate,
    cumulative_trials_per_cell,
    open_registry,
    record_trial,
)
from polaris.core.evolve.variants import (
    GRID_TOTAL_CAP,
    StrategyVariant,
    enumerate_grid,
    make_variant,
)

__all__ = [
    "GRID_TOTAL_CAP",
    "PARAM_BOUNDS",
    "CellAggregate",
    "PassRateAggregate",
    "StrategyVariant",
    "TrialRow",
    "VariantEval",
    "aggregate_pass_rate",
    "build_baselines",
    "cross_variant_sr_variance",
    "cumulative_trials_per_cell",
    "enumerate_grid",
    "evaluate_variant",
    "gate_can_discriminate",
    "honest_dsr",
    "make_variant",
    "min_passable_n",
    "open_registry",
    "record_trial",
    "split_is_oos",
    "varyable_params",
]
