"""Purged/embargo TIME-axis split generator (frontgate-scan item #10) — TDD.

DEMO/PAPER · behavior-0 · pure, no I/O. Mirrors ``walk_forward_splits``'s
equal-block partition + honest-degenerate-case contract, with the purge
predicate adapted from "index distance" to "interval overlap".
"""

from __future__ import annotations

from polaris.core.benchmark.purged_time_split import (
    TimedObservation,
    purged_time_splits,
)

DAY = 86_400


def _obs(obs_id: str, start_ts: int, end_ts: int) -> TimedObservation:
    return TimedObservation(obs_id=obs_id, start_ts=start_ts, end_ts=end_ts)


def _evenly_spaced(n: int, *, spacing: int = DAY, duration: int = 3600) -> list[TimedObservation]:
    return [
        _obs(f"o{i}", start_ts=i * spacing, end_ts=i * spacing + duration)
        for i in range(n)
    ]


def test_degenerate_zero_splits_returns_empty() -> None:
    assert purged_time_splits(_evenly_spaced(10), n_splits=0, embargo_sec=0) == ()


def test_degenerate_empty_observations_returns_empty() -> None:
    assert purged_time_splits([], n_splits=1, embargo_sec=0) == ()


def test_degenerate_too_few_observations_for_requested_splits() -> None:
    assert purged_time_splits(_evenly_spaced(2), n_splits=5, embargo_sec=0) == ()


def test_basic_split_partitions_without_overlap() -> None:
    obs = _evenly_spaced(30, duration=60)  # short trades, no embargo overlap risk
    splits = purged_time_splits(obs, n_splits=2, embargo_sec=0)
    assert len(splits) == 2
    for s in splits:
        assert set(s.is_ids).isdisjoint(set(s.oos_ids))
        assert len(s.is_ids) > 0
        assert len(s.oos_ids) > 0


def test_anchored_grows_is_window_across_folds() -> None:
    obs = _evenly_spaced(40, duration=60)
    splits = purged_time_splits(obs, n_splits=3, embargo_sec=0, anchored=True)
    assert len(splits) == 3
    sizes = [len(s.is_ids) for s in splits]
    assert sizes == sorted(sizes)  # non-decreasing IS size


def test_boundary_straddling_trade_is_purged_from_both_sides() -> None:
    """The core purge guarantee: an observation whose HOLDING PERIOD spans the
    split boundary must be dropped from BOTH the IS and OOS candidate blocks —
    it must not appear in either side's output."""
    # 20 short observations, evenly spaced by 1 day, each lasting 1 hour —
    # n_splits=1 -> is_seed=10, oos_block=10, boundary_ts = obs[10].start_ts.
    obs = _evenly_spaced(20, duration=3600)
    boundary_ts = obs[10].start_ts
    # Inject one long-holding trade whose interval STRADDLES the boundary:
    # starts well before, ends well after.
    straddler = _obs("straddler", start_ts=boundary_ts - DAY, end_ts=boundary_ts + DAY)
    all_obs = [*obs, straddler]

    splits = purged_time_splits(all_obs, n_splits=1, embargo_sec=DAY // 2)
    assert len(splits) == 1
    s = splits[0]
    assert "straddler" not in s.is_ids
    assert "straddler" not in s.oos_ids


def test_non_straddling_trade_outside_embargo_survives() -> None:
    """A trade entirely clear of the embargo window on the IS side is kept."""
    obs = _evenly_spaced(20, duration=3600)
    splits = purged_time_splits(obs, n_splits=1, embargo_sec=1800)
    assert len(splits) == 1
    s = splits[0]
    # The very first observation is far from the boundary -> must survive.
    assert "o0" in s.is_ids


def test_zero_embargo_is_a_pure_equal_block_partition() -> None:
    obs = _evenly_spaced(20, duration=1)  # negligible duration -> no overlap
    splits = purged_time_splits(obs, n_splits=1, embargo_sec=0)
    assert len(splits) == 1
    s = splits[0]
    assert len(s.is_ids) == 10
    assert len(s.oos_ids) == 10
