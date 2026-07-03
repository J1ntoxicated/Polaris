"""Layer 5 — Performance-Tiered Strategy classes (pts-classes, group B/C).

``score_F`` (fee-normalized edge) classification logic. Storage layer
(``strategy_class`` table, hydrate/bootstrap) is group A's
(``polaris.core.lifecycle.recover_classes``); this package owns the score_F
formula/rollup (group B) and the transition state machine (group C) only.
"""

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
    evaluate_transition,
)

__all__ = [
    "FTrackCapResult",
    "LifecycleFee",
    "ScoreFResult",
    "TransitionInput",
    "TransitionResult",
    "compute_score_f",
    "evaluate_transition",
    "f_track_cap",
    "rollup_score_f",
]
