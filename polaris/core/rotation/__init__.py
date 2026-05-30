"""Capital-rotation evaluator package — pure opportunity-cost redeploy logic.

DEMO/PAPER ONLY. Re-exports the pure evaluator surface (no I/O; all DB/venue
values injected). See :mod:`polaris.core.rotation.evaluator` for the full
correctness contract (expected-dollar-edge comparison, additive margin,
posterior lower credible bound, winner-exempt, per-venue isolation).
"""

from __future__ import annotations

from polaris.core.rotation.evaluator import (
    POSTERIOR_MIN_N,
    ROTATION_CREDIBLE_LEVEL,
    ROTATION_DEFAULT_IMPROVEMENT_MARGIN_USD,
    ROTATION_MAX_PER_TICK,
    ROTATION_MIN_HOLD_SEC,
    CellStats,
    HeldPosition,
    RotationCandidate,
    choose_rotation,
    expected_dollar_edge_held,
    expected_dollar_edge_new,
    forward_score,
    held_forward_score,
    rotation_cost_usd,
    select_weakest,
    should_rotate,
)

__all__ = [
    "POSTERIOR_MIN_N",
    "ROTATION_CREDIBLE_LEVEL",
    "ROTATION_DEFAULT_IMPROVEMENT_MARGIN_USD",
    "ROTATION_MAX_PER_TICK",
    "ROTATION_MIN_HOLD_SEC",
    "CellStats",
    "HeldPosition",
    "RotationCandidate",
    "choose_rotation",
    "expected_dollar_edge_held",
    "expected_dollar_edge_new",
    "forward_score",
    "held_forward_score",
    "rotation_cost_usd",
    "select_weakest",
    "should_rotate",
]
