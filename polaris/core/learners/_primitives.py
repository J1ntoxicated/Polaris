"""Layer 5 learner primitives — constants, result dataclasses, clip helpers.

Pure value objects + math used across every learner (``session``/``regime``/
``max_hold``) and the sizing pipeline. Split out of ``base.py`` to keep each
module ≤500 LOC; ``base`` re-exports all of these so existing import paths
(``from polaris.core.learners.base import ClosedTrade``) keep working.

Spec source: vault/30_components/layer-5-learner-network.md (Q1-Q6); ADR-007.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Constants (P0)  — see vault/30_components/layer-5-learner-network.md
# ---------------------------------------------------------------------------

LEARNER_MIN_NEFF_FOR_DELTA: Final[float] = 20.0
"""Minimum n_eff before a learner is allowed to commit a delta."""

LEARNER_DELTA_HOURLY_CAP: Final[float] = 0.1
"""Per-key per-hour absolute delta ceiling (sanity bound)."""

LEARNER_INDIVIDUAL_MULT_CLIP: Final[tuple[float, float]] = (0.3, 3.0)
"""ADR-007 individual multiplier clip — saturation guard."""

LEARNER_PRODUCT_CLIP: Final[tuple[float, float]] = (0.1, 5.0)
"""ADR-007 final composed multiplier clip."""

WR_PROMOTE_THRESHOLD: Final[float] = 0.55
"""regime_mult promotion threshold (≥55% → +0.1)."""

WR_DEMOTE_THRESHOLD: Final[float] = 0.40
"""regime_mult demotion threshold (≤40% → -0.1)."""

TRIPLE_BLOCK_NEFF_THRESHOLD: Final[float] = 20.0
"""Minimum trades before triple block evaluates."""

TRIPLE_BLOCK_WR_THRESHOLD: Final[float] = 0.30
"""WR ceiling that triggers a triple block."""

TRIPLE_BLOCK_EXPECTANCY_THRESHOLD: Final[float] = -0.25
"""Expectancy R floor that triggers a triple block."""

TRIPLE_BLOCK_DURATION_SEC: Final[int] = 3600
"""Auto-unblock interval (1h) — ADR-007 principle 2."""

TRIPLE_BLOCK_SIZE_MULT: Final[float] = 0.3
"""Block applied as a size multiplier (entry remains allowed)."""

NEUTRAL_MULT: Final[float] = 1.0
"""Default fallback multiplier when sparse / disabled."""

DEFAULT_MAX_HOLD_FALLBACK_BARS: Final[int] = 60
"""max_hold fallback when no StrategyMetadata.expected_holding_bars."""

LEARNER_SNAPSHOT_DIR: Final[Path] = Path("data/learner_snapshots")
"""Filesystem location of SQLite hot-backup + JSON manifest snapshots
(spec: vault/30_components/layer-5-learner-network.md §Q3)."""

# ---------------------------------------------------------------------------
# Edge-validation Phase 1 — cost overlay + verdict thresholds (measure-only,
# never wired into sizing; aggressive-bias / 9-stack guard preserved).
# ---------------------------------------------------------------------------

COST_BPS_CAPITAL: Final[float] = 3.0
"""Per-leg cost (bps of notional) assumed for Capital CFD demo — the demo
venue reports ``fee=0`` so a tunable constant stands in for the implicit
spread/financing. Applied to *both* legs in the cost overlay."""

COST_BPS_OKX_FALLBACK: Final[float] = 1.0
"""Per-leg fallback cost (bps) for OKX when a fill row carries no slippage —
real OKX fills already include fee + slippage, so this is only a floor."""

EDGE_VERDICT_TAU_HI: Final[float] = 0.85
"""P(expectancy>0) ceiling at/above which a bucket is labelled
``validated-alpha`` (display label only — NOT a sizing gate)."""

EDGE_VERDICT_TAU_LO: Final[float] = 0.15
"""P(expectancy>0) floor at/below which a bucket is labelled ``anti-edge``."""

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """Trade close event consumed by every learner update."""

    trade_id: str
    strategy_id: str
    ticker: str
    venue: str
    regime: str
    session: str
    pnl_r: float
    won: bool
    holding_bars: int
    closed_ts: int


@dataclass(frozen=True, slots=True)
class LearnerMultResult:
    """``get_mult`` output, surfacing fallback/source for audit."""

    value: float
    source: str  # "live" / "fallback_neutral" / "disabled" / "sparse"
    n_eff: float = 0.0


@dataclass(frozen=True, slots=True)
class TripleBlockEntry:
    """Active triple block row."""

    ticker: str
    strategy_id: str
    regime: str
    size_mult: float
    reason: str
    source_learner: str
    blocked_until_ts: int
    created_ts: int


@dataclass(slots=True)
class HourlyCommitReport:
    """Outcome from one ``commit_hourly`` invocation (audit + smoke)."""

    learner_id: str
    keys_updated: int
    deltas_applied: dict[str, float] = field(default_factory=dict)
    snapshot_ts: int = 0


# ---------------------------------------------------------------------------
# Clip helpers
# ---------------------------------------------------------------------------


def clip_individual_mult(value: float) -> float:
    """Clip a single learner multiplier to ``LEARNER_INDIVIDUAL_MULT_CLIP``."""
    if not math.isfinite(value):
        return NEUTRAL_MULT
    lo, hi = LEARNER_INDIVIDUAL_MULT_CLIP
    return max(lo, min(hi, value))


def clip_product_mult(value: float) -> float:
    """Clip the composed product multiplier to ``LEARNER_PRODUCT_CLIP``."""
    if not math.isfinite(value):
        return NEUTRAL_MULT
    lo, hi = LEARNER_PRODUCT_CLIP
    return max(lo, min(hi, value))


def clip_hourly_delta(delta: float) -> float:
    """Clip a single-hour delta proposal to ``±LEARNER_DELTA_HOURLY_CAP``."""
    if not math.isfinite(delta):
        return 0.0
    return max(-LEARNER_DELTA_HOURLY_CAP, min(LEARNER_DELTA_HOURLY_CAP, delta))


def resolve_final_size_mult(
    *,
    session_mult: float,
    regime_mult: float,
    cell_routing_mult: float,
    ai_feedback_weight: float = NEUTRAL_MULT,
) -> float:
    """Independent-axis composition (vault Q2 — multiplicative chain).

    Each input is individually clipped before composition; the product is then
    re-clipped to keep the cumulative multiplier sane.
    """
    s = clip_individual_mult(session_mult)
    r = clip_individual_mult(regime_mult)
    c = clip_individual_mult(cell_routing_mult)
    a = clip_individual_mult(ai_feedback_weight)
    return clip_product_mult(s * r * c * a)
