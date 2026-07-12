"""Layer 5 — Performance-Tiered Strategy classes (pts-classes, group B/C/E).

``score_F`` (fee-normalized edge) classification logic. Storage layer
(``strategy_class`` table, hydrate/bootstrap) is group A's
(``polaris.core.lifecycle.recover_classes``); this package owns the score_F
formula/rollup (group B), the transition state machine (group C), and the
R-pool intra-track allocator (group E) only.
"""

from polaris.core.classes.r_pool import (
    PROBE_CONCURRENCY_CAP,
    EarnAllocation,
    RPoolResult,
    TrackMember,
    allocate_r_pool,
)
from polaris.core.classes.score_f import (
    FTrackCapResult,
    LifecycleFee,
    ScoreFResult,
    compute_score_f,
    f_track_cap,
    rollup_score_f,
)
from polaris.core.classes.transition import (
    TransitionInput,
    TransitionResult,
    TransitionThresholds,
    evaluate_transition,
)

__all__ = [
    "PROBE_CONCURRENCY_CAP",
    "EarnAllocation",
    "FTrackCapResult",
    "LifecycleFee",
    "RPoolResult",
    "ScoreFResult",
    "TrackMember",
    "TransitionInput",
    "TransitionResult",
    "TransitionThresholds",
    "allocate_r_pool",
    "compute_score_f",
    "evaluate_transition",
    "f_track_cap",
    "rollup_score_f",
]
