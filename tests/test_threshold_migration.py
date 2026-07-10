"""Tests for threshold_migration (fee-split v0 — ECDF percentile preservation
+ isotonic order enforcement). DEMO/PAPER only. Spec:
vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 item 5.
"""
from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from polaris.core.classes.threshold_migration import (
    ecdf_percentile,
    enforce_isotonic,
    migrate_thresholds,
    value_at_percentile,
)

# ---------------------------------------------------------------------------
# ecdf_percentile / value_at_percentile — round-trip
# ---------------------------------------------------------------------------


def test_ecdf_percentile_median_of_uniform_population():
    population = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert ecdf_percentile(3.0, population) == pytest.approx(0.6)  # 3/5 at-or-below


def test_ecdf_percentile_below_all_is_zero():
    assert ecdf_percentile(-100.0, [1.0, 2.0, 3.0]) == pytest.approx(0.0)


def test_ecdf_percentile_above_all_is_one():
    assert ecdf_percentile(100.0, [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_ecdf_percentile_empty_population_is_neutral_midpoint():
    assert ecdf_percentile(5.0, []) == pytest.approx(0.5)


def test_value_at_percentile_zero_is_min():
    population = [3.0, 1.0, 5.0, 2.0, 4.0]
    assert value_at_percentile(population, 0.0) == pytest.approx(1.0)


def test_value_at_percentile_one_is_max():
    population = [3.0, 1.0, 5.0, 2.0, 4.0]
    assert value_at_percentile(population, 1.0) == pytest.approx(5.0)


def test_value_at_percentile_empty_population_is_zero():
    assert value_at_percentile([], 0.5) == 0.0


def test_round_trip_preserves_approximate_rank():
    population = list(range(1, 101))  # 1..100
    pct = ecdf_percentile(25.0, [float(p) for p in population])
    mapped = value_at_percentile([float(p) for p in population], pct)
    assert mapped == pytest.approx(25.0, abs=2.0)


# ---------------------------------------------------------------------------
# enforce_isotonic (PAVA)
# ---------------------------------------------------------------------------


def test_isotonic_already_monotonic_unchanged():
    values = [1.0, 2.0, 3.0]
    assert enforce_isotonic(values, reference=values) == pytest.approx(values)


def test_isotonic_repairs_a_single_violation():
    """[-4, -1, -3] is not non-decreasing (last two cross) — PAVA must pool
    the violating adjacent pair into their mean, keeping monotonic order."""
    values = [-4.0, -1.0, -3.0]
    result = enforce_isotonic(values, reference=[-4.0, -3.0, -1.0])  # ref is increasing
    assert result[0] <= result[1] <= result[2]
    # the crossing pair (-1, -3) pools to their mean (-2.0)
    assert result[1] == pytest.approx(-2.0)
    assert result[2] == pytest.approx(-2.0)


def test_isotonic_respects_decreasing_reference_direction():
    reference = [10.0, 5.0, 1.0]  # decreasing
    values = [10.0, 1.0, 5.0]  # violates decreasing order
    result = enforce_isotonic(values, reference=reference)
    assert result[0] >= result[1] >= result[2]


def test_isotonic_single_or_empty_passthrough():
    assert enforce_isotonic([], reference=[]) == []
    assert enforce_isotonic([5.0], reference=[5.0]) == [5.0]


@given(st.lists(st.floats(min_value=-100.0, max_value=100.0, allow_nan=False), min_size=2, max_size=10))
def test_property_isotonic_output_always_monotonic_nondecreasing_reference(
    values: list[float],
) -> None:
    reference = sorted(values)  # non-decreasing reference
    result = enforce_isotonic(values, reference=reference)
    assert all(a <= b + 1e-9 for a, b in zip(result, result[1:], strict=False))


# ---------------------------------------------------------------------------
# migrate_thresholds — end to end
# ---------------------------------------------------------------------------


def test_migrate_preserves_relative_percentile_ordering():
    """Schmitt-style thresholds (increasing: BENCH-partial < BENCH-full <
    PROVE) must migrate onto the new scale in the SAME order."""
    old_population = [float(x) for x in range(-100, 101)]  # -100..100
    new_population = [float(x) * 2 for x in range(-50, 51)]  # -100..100 (2x spacing, same range)
    old_thresholds = [-4.0, -3.0, -1.0]  # Schmitt order
    migrated = migrate_thresholds(
        old_thresholds, old_population=old_population, new_population=new_population,
    )
    assert migrated[0] <= migrated[1] <= migrated[2]


def test_migrate_onto_shifted_population_tracks_shift():
    """A new population that is uniformly shifted +1000 vs the old one
    should migrate old thresholds to roughly +1000 as well (percentile rank
    preserved -> absolute value tracks the new population's scale)."""
    old_population = [float(x) for x in range(0, 101)]  # 0..100
    new_population = [float(x) + 1000.0 for x in range(0, 101)]  # 1000..1100
    migrated = migrate_thresholds(
        [25.0, 50.0, 75.0], old_population=old_population, new_population=new_population,
    )
    assert migrated[0] == pytest.approx(1025.0, abs=2.0)
    assert migrated[1] == pytest.approx(1050.0, abs=2.0)
    assert migrated[2] == pytest.approx(1075.0, abs=2.0)


def test_migrate_empty_populations_degenerate_but_no_crash():
    result = migrate_thresholds([-1.0, 0.0, 1.0], old_population=[], new_population=[])
    assert len(result) == 3
    assert result[0] <= result[1] <= result[2]
