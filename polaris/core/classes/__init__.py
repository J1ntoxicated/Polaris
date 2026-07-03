"""Layer 5 — Performance-Tiered Strategy classes (pts-classes, group B).

``score_F`` (fee-normalized edge) classification logic. Storage layer
(``strategy_class`` table, hydrate/bootstrap) is group A's
(``polaris.core.lifecycle.recover_classes``); this package owns the score_F
formula and its daily rollup only.
"""

from polaris.core.classes.score_f import (
    FTrackCapResult,
    LifecycleFee,
    ScoreFResult,
    compute_score_f,
    f_track_cap,
    rollup_score_f,
)

__all__ = [
    "FTrackCapResult",
    "LifecycleFee",
    "ScoreFResult",
    "compute_score_f",
    "f_track_cap",
    "rollup_score_f",
]
