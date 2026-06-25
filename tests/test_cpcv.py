"""Combinatorial Purged Cross-Validation TDD (Lopez de Prado AFML ch.12).

CPCV partitions the bar index into ``n_groups`` contiguous groups and takes
EVERY ``C(n_groups, k_test)`` combination of groups as the test set (the rest =
train). Bars within ``embargo_bars`` of any test-group boundary are purged from
train so no observation straddles the split (leakage block — same embargo
concept the existing ``walk_forward`` proved). The many overlapping test
combinations yield multiple backtest PATHS — the population PBO consumes.
"""

from __future__ import annotations

import math

from polaris.core.benchmark.cpcv import CPCVSplit, cpcv_splits


def _comb(n: int, k: int) -> int:
    return math.comb(n, k)


# --- split count = C(G, k) -------------------------------------------------
def test_split_count_is_n_choose_k() -> None:
    splits = cpcv_splits(n_bars=600, n_groups=6, k_test=2, embargo_bars=5)
    assert len(splits) == _comb(6, 2)  # 15 combinations
    assert all(isinstance(s, CPCVSplit) for s in splits)


# --- train / test are disjoint and embargo-purged --------------------------
def test_train_test_disjoint_and_embargoed() -> None:
    embargo = 5
    splits = cpcv_splits(n_bars=600, n_groups=6, k_test=2, embargo_bars=embargo)
    for s in splits:
        train = set(s.train_index)
        test = set(s.test_index)
        # Disjoint.
        assert train.isdisjoint(test)
        # No train bar lies within ``embargo`` of any test bar (purge gap).
        for t in test:
            for d in range(1, embargo + 1):
                assert (t - d) not in train
                assert (t + d) not in train


# --- every group appears as test in C(G-1, k-1) combinations ---------------
def test_each_group_tested_in_expected_number_of_paths() -> None:
    n_groups, k = 6, 2
    splits = cpcv_splits(n_bars=600, n_groups=n_groups, k_test=k, embargo_bars=0)
    appearances: dict[int, int] = {}
    for s in splits:
        for g in s.test_group_ids:
            appearances[g] = appearances.get(g, 0) + 1
    expected = _comb(n_groups - 1, k - 1)  # each group is in C(G-1,k-1) combos
    assert set(appearances) == set(range(n_groups))
    assert all(v == expected for v in appearances.values())


# --- test index is exactly the union of its groups' bar ranges -------------
def test_test_index_covers_its_groups() -> None:
    splits = cpcv_splits(n_bars=60, n_groups=6, k_test=1, embargo_bars=0)
    # 6 single-group test folds covering the whole index with no overlap.
    assert len(splits) == 6
    covered: set[int] = set()
    for s in splits:
        covered |= set(s.test_index)
    assert covered == set(range(60))


# --- infeasible -> () (honest degenerate, no silent shrink) ----------------
def test_infeasible_returns_empty() -> None:
    assert cpcv_splits(n_bars=3, n_groups=6, k_test=2, embargo_bars=0) == ()
    assert cpcv_splits(n_bars=600, n_groups=2, k_test=2, embargo_bars=0) == ()
    assert cpcv_splits(n_bars=600, n_groups=6, k_test=0, embargo_bars=0) == ()
    assert cpcv_splits(n_bars=600, n_groups=1, k_test=1, embargo_bars=0) == ()


# --- determinism -----------------------------------------------------------
def test_cpcv_determinism() -> None:
    a = cpcv_splits(n_bars=600, n_groups=6, k_test=2, embargo_bars=5)
    b = cpcv_splits(n_bars=600, n_groups=6, k_test=2, embargo_bars=5)
    assert a == b
