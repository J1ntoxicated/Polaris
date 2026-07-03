"""TDD — pts-classes (group E) R-pool allocator.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
``allocate_r_pool`` is a pure capital-ROUTING function: BENCH-freed R is
redistributed to EARN tracks in the SAME track (intra-track only — cross-track
is the profit-sweep ladder's job, no dual plumbing). Zero-EARN tracks leave
the freed R idle rather than forcing probe expansion (no_block_filter /
aggressive_always_profit — idle is a routing outcome, not a defensive
throttle). No new T4 multiplier: this pool is an ADDITIVE headroom pool
consumed by ``engine.headroom_min`` alongside its existing min() terms.
"""

from __future__ import annotations

import math

import pytest

from polaris.core.classes.r_pool import (
    PROBE_CONCURRENCY_CAP,
    EarnAllocation,
    RPoolResult,
    TrackMember,
    allocate_r_pool,
)

# ---------------------------------------------------------------------------
# probe overhead pre-deduction
# ---------------------------------------------------------------------------


def test_probe_overhead_deducted_before_earn_split() -> None:
    """Track A concurrency cap = 3 x probe_notional_usd deducted FIRST from
    bench_freed_usd; only the remainder is EARN-weighted."""
    result = allocate_r_pool(
        track="A",
        bench_freed_usd=1_000.0,
        probe_notional_usd=100.0,
        members=[TrackMember(strategy_id="s1", strategy_class="EARN", score_f_w=2.0)],
    )
    assert result.probe_overhead_usd == 300.0  # 3 x 100
    assert result.residual_usd == 700.0
    assert result.allocations == [EarnAllocation(strategy_id="s1", allocated_usd=700.0)]


def test_probe_overhead_never_exceeds_bench_freed() -> None:
    """Overhead cannot go negative-residual — clipped at bench_freed_usd
    (min() composition, not a new multiplier)."""
    result = allocate_r_pool(
        track="C",
        bench_freed_usd=50.0,
        probe_notional_usd=100.0,
        members=[TrackMember(strategy_id="s1", strategy_class="EARN", score_f_w=1.0)],
    )
    # Track C cap = 1 x 100 = 100, but only 50 available.
    assert result.probe_overhead_usd == 50.0
    assert result.residual_usd == 0.0
    assert result.allocations == []
    assert result.idle_usd == 0.0


@pytest.mark.parametrize(
    "track,expected_cap_mult",
    [("A", 3), ("B", 2), ("C", 1)],
)
def test_probe_concurrency_cap_per_track(track: str, expected_cap_mult: int) -> None:
    assert PROBE_CONCURRENCY_CAP[track] == expected_cap_mult


# ---------------------------------------------------------------------------
# EARN weighted split — weight = max(score_F, 1)
# ---------------------------------------------------------------------------


def test_earn_weighted_split_proportional() -> None:
    result = allocate_r_pool(
        track="B",
        bench_freed_usd=1_000.0,
        probe_notional_usd=0.0,
        members=[
            TrackMember(strategy_id="s1", strategy_class="EARN", score_f_w=3.0),
            TrackMember(strategy_id="s2", strategy_class="EARN", score_f_w=1.0),
        ],
    )
    # weights: max(3,1)=3, max(1,1)=1 -> total 4 -> 750 / 250
    by_id = {a.strategy_id: a.allocated_usd for a in result.allocations}
    assert by_id["s1"] == pytest.approx(750.0)
    assert by_id["s2"] == pytest.approx(250.0)


def test_earn_weight_floors_at_one_for_negative_score_f() -> None:
    """weight = max(score_F_W, 1) — a negative/sub-1 score_F never zeroes or
    inverts an EARN track's share (floor, not a block)."""
    result = allocate_r_pool(
        track="A",
        bench_freed_usd=400.0,
        probe_notional_usd=0.0,
        members=[
            TrackMember(strategy_id="s1", strategy_class="EARN", score_f_w=-5.0),
            TrackMember(strategy_id="s2", strategy_class="EARN", score_f_w=1.0),
        ],
    )
    by_id = {a.strategy_id: a.allocated_usd for a in result.allocations}
    # both floor to weight=1 -> even split
    assert by_id["s1"] == pytest.approx(200.0)
    assert by_id["s2"] == pytest.approx(200.0)


def test_non_earn_members_excluded_from_split() -> None:
    """PROVE/BENCH/KILL members in the same track never receive an R-pool
    share — the pool only routes to EARN (capital-routing to the strongest
    tier, not a blanket track subsidy)."""
    result = allocate_r_pool(
        track="A",
        bench_freed_usd=300.0,
        probe_notional_usd=0.0,
        members=[
            TrackMember(strategy_id="earn1", strategy_class="EARN", score_f_w=1.0),
            TrackMember(strategy_id="prove1", strategy_class="PROVE", score_f_w=10.0),
            TrackMember(strategy_id="bench1", strategy_class="BENCH", score_f_w=10.0),
        ],
    )
    by_id = {a.strategy_id: a.allocated_usd for a in result.allocations}
    assert by_id == {"earn1": pytest.approx(300.0)}


# ---------------------------------------------------------------------------
# zero-EARN track -> residual idle, no forced probe expansion
# ---------------------------------------------------------------------------


def test_zero_earn_track_leaves_residual_idle() -> None:
    result = allocate_r_pool(
        track="A",
        bench_freed_usd=500.0,
        probe_notional_usd=50.0,
        members=[TrackMember(strategy_id="p1", strategy_class="PROVE", score_f_w=99.0)],
    )
    assert result.probe_overhead_usd == 150.0  # 3 x 50
    assert result.residual_usd == 350.0
    assert result.allocations == []
    assert result.idle_usd == 350.0


def test_zero_members_at_all_leaves_full_residual_idle() -> None:
    result = allocate_r_pool(
        track="B",
        bench_freed_usd=200.0,
        probe_notional_usd=0.0,
        members=[],
    )
    assert result.allocations == []
    assert result.idle_usd == 200.0


def test_nonzero_earn_split_has_zero_idle() -> None:
    result = allocate_r_pool(
        track="A",
        bench_freed_usd=300.0,
        probe_notional_usd=0.0,
        members=[TrackMember(strategy_id="s1", strategy_class="EARN", score_f_w=1.0)],
    )
    assert result.idle_usd == 0.0


# ---------------------------------------------------------------------------
# intra-track only — no cross-track leakage (structural guard)
# ---------------------------------------------------------------------------


def test_no_cross_track_param_exists() -> None:
    """allocate_r_pool has no target-track / other-track parameter — the
    signature itself enforces intra-track-only (cross-track capital movement
    is the profit-sweep ladder's exclusive job, no dual pipe)."""
    import inspect

    params = set(inspect.signature(allocate_r_pool).parameters)
    assert params == {"track", "bench_freed_usd", "probe_notional_usd", "members"}


# ---------------------------------------------------------------------------
# data integrity guards
# ---------------------------------------------------------------------------


def test_non_finite_bench_freed_treated_as_zero() -> None:
    result = allocate_r_pool(
        track="A",
        bench_freed_usd=float("nan"),
        probe_notional_usd=10.0,
        members=[TrackMember(strategy_id="s1", strategy_class="EARN", score_f_w=1.0)],
    )
    assert result.probe_overhead_usd == 0.0
    assert result.residual_usd == 0.0
    assert result.allocations == []
    assert result.idle_usd == 0.0


def test_negative_bench_freed_clamped_to_zero() -> None:
    result = allocate_r_pool(
        track="A",
        bench_freed_usd=-100.0,
        probe_notional_usd=10.0,
        members=[TrackMember(strategy_id="s1", strategy_class="EARN", score_f_w=1.0)],
    )
    assert result.residual_usd == 0.0


def test_non_finite_score_f_w_never_poisons_allocation_with_nan() -> None:
    """A corrupt/NaN score_F weight (e.g. an upstream rollup gap) must floor
    to the SAME weight=1 as any other sub-1 score — ``max(nan, 1)`` is
    order-dependent in Python and can silently return ``nan``, which would
    poison the whole track's dollar allocations (and everything they feed
    into) with NaN. Never a data-integrity crash into the sizing chain."""
    result = allocate_r_pool(
        track="B",
        bench_freed_usd=100.0,
        probe_notional_usd=0.0,
        members=[
            TrackMember(strategy_id="s1", strategy_class="EARN", score_f_w=float("nan")),
            TrackMember(strategy_id="s2", strategy_class="EARN", score_f_w=1.0),
        ],
    )
    by_id = {a.strategy_id: a.allocated_usd for a in result.allocations}
    assert math.isfinite(by_id["s1"])
    assert math.isfinite(by_id["s2"])
    assert by_id["s1"] == pytest.approx(50.0)
    assert by_id["s2"] == pytest.approx(50.0)


def test_result_is_frozen_dataclass_shape() -> None:
    result = allocate_r_pool(
        track="A", bench_freed_usd=0.0, probe_notional_usd=0.0, members=[]
    )
    assert isinstance(result, RPoolResult)
