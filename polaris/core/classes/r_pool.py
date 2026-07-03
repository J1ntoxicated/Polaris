"""R-pool allocator — pts-classes (Performance-Tiered Strategy classes),
group E.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
``allocate_r_pool`` is a pure capital-ROUTING decision function, same shape as
``transition.evaluate_transition`` — no DB I/O, no sizing/9-stack/-1.0R rail
touch. It only decides how much of a track's BENCH-freed R headroom (already
computed by the caller) flows to that SAME track's EARN members; capital
allocation elsewhere (``engine.headroom_min``) consumes the result as an
ADDITIVE min()-slot headroom pool, never a new T4 chain multiplier.

Scope discipline (task spec): BENCH-freed R routes to EARN strategies in the
SAME track only. Cross-track capital movement is the profit-sweep ladder's
job (``polaris.core.sizing.ladder`` — 1단 close -> 3단 Alpaca draw); routing
BOTH ways would be dual plumbing for the same capital-recycling concern, so
this module has no cross-track / target-track parameter at all (structural
guard — see ``test_no_cross_track_param_exists``).

Order of operations per call:
    1. Probe overhead pre-deduction: ``PROBE_CONCURRENCY_CAP[track] x
       probe_notional_usd`` comes off ``bench_freed_usd`` FIRST (reserves
       headroom for the track's own concurrent PROVE probes before any EARN
       gets a share) — clipped so overhead never exceeds what's available
       (``min()`` composition, not a new multiplier).
    2. The residual (if any) splits across EARN members ONLY, weighted by
       ``max(score_F_W, 1)`` — a sub-1 (including negative) score_F floors to
       weight 1 rather than shrinking/zeroing/inverting a still-EARN track's
       share (aggressive_always_profit: EARN membership itself is the
       admission ticket, weight only shapes the SPLIT).
    3. Zero EARN members in the track -> the full residual is reported
       ``idle_usd`` and NOT allocated. This is a routing outcome (nothing to
       route TO), not a defensive throttle — no forced probe-cap expansion,
       no reallocation outside the track (no_block_filter_architecture /
       flow_not_block: BENCH/PROVE members keep signaling/learning
       regardless; they just don't draw this pool).

Interpretation note (ambiguous-threshold precedent, same style as
``transition.py``'s docstring): the task header gives the concurrency cap as
"3/2/1" without naming which track gets which number. Track A (OKX
SPOT/crypto) = 3, Track B (Capital CFD) = 2, Track C (Alpaca US equity,
inert until SIP #42) = 1 — the natural A/B/C list order, matching every
other track-keyed literal in this codebase (``TRACK_A_GROSS_PCT`` etc., all
ordered A/B/C).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

__all__ = [
    "PROBE_CONCURRENCY_CAP",
    "EarnAllocation",
    "RPoolResult",
    "TrackMember",
    "allocate_r_pool",
]

Track = Literal["A", "B", "C"]

# See module docstring "Interpretation note" — A/B/C list-order mapping of the
# task spec's "concurrent probe cap 3/2/1".
PROBE_CONCURRENCY_CAP: Final[dict[str, int]] = {"A": 3, "B": 2, "C": 1}

# EARN-split weight floor — ``max(score_F_W, 1)`` per task spec.
_WEIGHT_FLOOR: Final[float] = 1.0


@dataclass(slots=True, frozen=True)
class TrackMember:
    """One (venue, strategy_id) track-member's classification + score_F
    weight, as assembled by the caller from ``strategy_class`` (group A
    storage) + ``score_f_events`` (group B rollup) — this module performs no
    DB read itself."""

    strategy_id: str
    strategy_class: str
    score_f_w: float


@dataclass(slots=True, frozen=True)
class EarnAllocation:
    """One EARN member's share of the residual pool, in USD."""

    strategy_id: str
    allocated_usd: float


@dataclass(slots=True, frozen=True)
class RPoolResult:
    """Full accounting for one ``allocate_r_pool`` call — every USD figure
    reconciles: ``bench_freed_usd_clamped == probe_overhead_usd +
    residual_usd`` and ``residual_usd == sum(allocations) + idle_usd``."""

    probe_overhead_usd: float
    residual_usd: float
    allocations: list[EarnAllocation]
    idle_usd: float


def _finite_nonneg(x: float) -> float:
    return x if math.isfinite(x) and x > 0.0 else 0.0


def _weight_floor(score_f_w: float) -> float:
    """``max(score_F_W, 1)`` — guarded against NaN. Plain ``max(nan, 1.0)`` is
    order-dependent in Python (``max(nan, 1.0) -> nan`` but ``max(1.0, nan)
    -> 1.0``) and would silently poison this member's (and via total_weight,
    every sibling's) dollar allocation with NaN on a corrupt/missing score_F
    read. A non-finite weight floors to 1.0 exactly like every other sub-1
    score (data-integrity fallback, never a crash into the sizing chain)."""
    if not math.isfinite(score_f_w):
        return _WEIGHT_FLOOR
    return max(score_f_w, _WEIGHT_FLOOR)


def allocate_r_pool(
    *,
    track: Track,
    bench_freed_usd: float,
    probe_notional_usd: float,
    members: list[TrackMember],
) -> RPoolResult:
    """Allocate one track's BENCH-freed R headroom to its EARN members.

    Intra-track only (see module docstring) — no target-track parameter
    exists on this function by design.
    """
    freed = _finite_nonneg(bench_freed_usd)
    probe_unit = _finite_nonneg(probe_notional_usd)

    probe_overhead = min(freed, PROBE_CONCURRENCY_CAP[track] * probe_unit)
    residual = freed - probe_overhead

    earn_members = [m for m in members if m.strategy_class == "EARN"]
    if not earn_members or residual <= 0.0:
        return RPoolResult(
            probe_overhead_usd=probe_overhead,
            residual_usd=residual,
            allocations=[],
            idle_usd=residual,
        )

    weights = [_weight_floor(m.score_f_w) for m in earn_members]
    total_weight = sum(weights)
    allocations = [
        EarnAllocation(
            strategy_id=m.strategy_id,
            allocated_usd=residual * (w / total_weight),
        )
        for m, w in zip(earn_members, weights, strict=True)
    ]
    return RPoolResult(
        probe_overhead_usd=probe_overhead,
        residual_usd=residual,
        allocations=allocations,
        idle_usd=0.0,
    )
