"""Layer 5 — Learner Network registry.

Spec sources:
- vault/30_components/layer-5-learner-network.md
- vault/10_decisions/ADR-007-learner-network.md

P0 active learners: ``session_mult`` / ``regime_mult`` / ``max_hold``.
P1 stubs (not exported from registry): ``profit_target`` / ``trail_mult`` /
``bep_activate`` / ``ai_feedback``.
"""

from __future__ import annotations

from polaris.core.learners.base import (
    DEFAULT_MAX_HOLD_FALLBACK_BARS,
    LEARNER_DELTA_HOURLY_CAP,
    LEARNER_INDIVIDUAL_MULT_CLIP,
    LEARNER_MIN_NEFF_FOR_DELTA,
    LEARNER_PRODUCT_CLIP,
    LEARNER_SNAPSHOT_DIR,
    NEUTRAL_MULT,
    TRIPLE_BLOCK_DURATION_SEC,
    TRIPLE_BLOCK_SIZE_MULT,
    WR_DEMOTE_THRESHOLD,
    WR_PROMOTE_THRESHOLD,
    BaseLearner,
    ClosedTrade,
    HourlyCommitReport,
    LearnerMultResult,
    TripleBlockEntry,
    clip_hourly_delta,
    clip_individual_mult,
    clip_product_mult,
    evaluate_triple_block,
    evict_expired_triple_blocks,
    resolve_final_size_mult,
    restore_snapshot_from_disk,
)
from polaris.core.learners.max_hold import BUCKET_FACTORS, MaxHoldLearner
from polaris.core.learners.regime import RegimeMultLearner
from polaris.core.learners.scheduler import (
    DEFAULT_INTERVAL_SEC,
    LearnerScheduler,
    TuneCycleReport,
)
from polaris.core.learners.session import SessionMultLearner

# Registry: learner_id → class. P0 = 3 priority.
P0_LEARNERS: dict[str, type[BaseLearner]] = {
    "session_mult": SessionMultLearner,
    "regime_mult": RegimeMultLearner,
    "max_hold": MaxHoldLearner,
}


__all__ = [
    "BUCKET_FACTORS",
    "BaseLearner",
    "ClosedTrade",
    "DEFAULT_INTERVAL_SEC",
    "DEFAULT_MAX_HOLD_FALLBACK_BARS",
    "HourlyCommitReport",
    "LEARNER_DELTA_HOURLY_CAP",
    "LEARNER_INDIVIDUAL_MULT_CLIP",
    "LEARNER_MIN_NEFF_FOR_DELTA",
    "LEARNER_PRODUCT_CLIP",
    "LEARNER_SNAPSHOT_DIR",
    "LearnerMultResult",
    "LearnerScheduler",
    "MaxHoldLearner",
    "NEUTRAL_MULT",
    "P0_LEARNERS",
    "RegimeMultLearner",
    "SessionMultLearner",
    "TRIPLE_BLOCK_DURATION_SEC",
    "TRIPLE_BLOCK_SIZE_MULT",
    "TripleBlockEntry",
    "TuneCycleReport",
    "WR_DEMOTE_THRESHOLD",
    "WR_PROMOTE_THRESHOLD",
    "clip_hourly_delta",
    "clip_individual_mult",
    "clip_product_mult",
    "evaluate_triple_block",
    "evict_expired_triple_blocks",
    "resolve_final_size_mult",
    "restore_snapshot_from_disk",
]
