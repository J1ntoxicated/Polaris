"""Combinatorial Purged Cross-Validation splits over bar index (pure, no I/O).

Lopez de Prado, *Advances in Financial Machine Learning* ch.12. CPCV generalises
the sequential purged walk-forward (``walk_forward.py``): instead of one marching
OOS block, it partitions the bar index into ``n_groups`` contiguous groups and
takes EVERY ``C(n_groups, k_test)`` combination of groups as the test set (the
remaining groups = train). Because each group appears as test in many
combinations, CPCV yields multiple overlapping backtest **paths** — the
population the Probability of Backtest Overfitting (``overfit.pbo``) consumes.

Leakage block (same embargo concept ``walk_forward`` proved): every train bar
within ``embargo_bars`` of a test-group boundary is purged, so no observation
straddles a split. Params are FROZEN — the train side only selects which
cell/strategy is "live"; the test side measures held-out edge.

Honest degenerate case: returns ``()`` when the index cannot host the requested
partition (no silent shrink) — exactly like ``walk_forward_splits``.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

__all__ = ["CPCVSplit", "cpcv_splits"]


@dataclass(frozen=True, slots=True)
class CPCVSplit:
    """One combinatorial fold: train + (purged) test bar indices.

    ``train_index`` / ``test_index`` are sorted tuples of 0-based bar positions
    into the shared bar sequence. ``test_group_ids`` is the sorted tuple of group
    ordinals that form the test set (each group appears as test in ``C(G-1,k-1)``
    folds — the path-coverage property PBO relies on). ``split_id`` is the
    enumeration ordinal of the combination (a deterministic count, not a date).
    """

    split_id: int
    train_index: tuple[int, ...]
    test_index: tuple[int, ...]
    test_group_ids: tuple[int, ...]


def _group_bounds(n_bars: int, n_groups: int) -> list[tuple[int, int]]:
    """Contiguous ``[start, end)`` bar ranges for each group (front-loaded
    remainder so groups differ by at most one bar — deterministic)."""
    base, extra = divmod(n_bars, n_groups)
    bounds: list[tuple[int, int]] = []
    start = 0
    for g in range(n_groups):
        size = base + (1 if g < extra else 0)
        bounds.append((start, start + size))
        start += size
    return bounds


def cpcv_splits(
    *, n_bars: int, n_groups: int, k_test: int, embargo_bars: int
) -> tuple[CPCVSplit, ...]:
    """Generate all ``C(n_groups, k_test)`` purged combinatorial folds.

    Each fold's test set = the union of ``k_test`` groups' bar ranges; the train
    set = every OTHER bar, MINUS any bar within ``embargo_bars`` of a test-group
    boundary (purge). Returns ``()`` for any infeasible request (too few bars to
    give every group at least one bar, ``k_test`` out of ``[1, n_groups-1]``, or a
    non-negative embargo) — honest, never a silent shrink.
    """
    if (
        n_bars < 1
        or n_groups < 2
        or k_test < 1
        or k_test > n_groups - 1
        or embargo_bars < 0
        or n_bars < n_groups  # every group needs at least one bar
    ):
        return ()
    bounds = _group_bounds(n_bars, n_groups)
    splits: list[CPCVSplit] = []
    for split_id, test_groups in enumerate(combinations(range(n_groups), k_test)):
        test_set: set[int] = set()
        purge: set[int] = set()
        for g in test_groups:
            lo, hi = bounds[g]
            test_set.update(range(lo, hi))
            # Purge the embargo halo on BOTH sides of each test group.
            purge.update(range(max(0, lo - embargo_bars), lo))
            purge.update(range(hi, min(n_bars, hi + embargo_bars)))
        train_set = set(range(n_bars)) - test_set - purge
        splits.append(
            CPCVSplit(
                split_id=split_id,
                train_index=tuple(sorted(train_set)),
                test_index=tuple(sorted(test_set)),
                test_group_ids=tuple(test_groups),
            )
        )
    return tuple(splits)
