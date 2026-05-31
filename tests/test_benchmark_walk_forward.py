"""Walk-forward TDD — purged/embargo splits over BAR INDEX (NOT a calendar gate).

Spec §Walk-forward: anchored/rolling purged splits; IS↔OOS embargo =
warmup_bars + max_ttl bars dropped between the windows (leakage block). The
split count is a WINDOW count, never a "weeks elapsed" gate. TDD #7: the embargo
bars are excluded from BOTH IS and OOS, and IS/OOS index sets never overlap.
"""

from __future__ import annotations

from polaris.core.benchmark.walk_forward import walk_forward_splits


def test_is_oos_indices_never_overlap() -> None:
    splits = walk_forward_splits(n_bars=100, n_splits=3, embargo_bars=5)
    assert splits
    for s in splits:
        assert set(s.is_index).isdisjoint(set(s.oos_index))


def test_embargo_bars_excluded_from_both() -> None:
    splits = walk_forward_splits(n_bars=100, n_splits=3, embargo_bars=5)
    for s in splits:
        # The embargo gap sits between the last IS bar and first OOS bar.
        last_is = max(s.is_index)
        first_oos = min(s.oos_index)
        gap = set(range(last_is + 1, first_oos))
        assert len(gap) == 5
        # No embargo index appears in either window.
        assert gap.isdisjoint(set(s.is_index))
        assert gap.isdisjoint(set(s.oos_index))


def test_anchored_is_grows_oos_walks_forward() -> None:
    splits = walk_forward_splits(n_bars=120, n_splits=3, embargo_bars=4, anchored=True)
    # Anchored: every IS window starts at 0 and grows; OOS windows march forward.
    assert all(min(s.is_index) == 0 for s in splits)
    oos_starts = [min(s.oos_index) for s in splits]
    assert oos_starts == sorted(oos_starts)
    assert len(set(oos_starts)) == len(oos_starts)  # strictly forward


def test_rolling_is_window_slides() -> None:
    splits = walk_forward_splits(n_bars=120, n_splits=3, embargo_bars=4, anchored=False)
    is_starts = [min(s.is_index) for s in splits]
    # Rolling: IS start moves forward across splits.
    assert is_starts == sorted(is_starts)
    assert is_starts[0] == 0
    assert is_starts[-1] > 0


def test_embargo_is_warmup_plus_max_ttl() -> None:
    # The caller passes embargo_bars = warmup_bars + max_ttl (spec). Verify the
    # generator honours exactly that many dropped bars.
    warmup, max_ttl = 12, 8
    splits = walk_forward_splits(n_bars=200, n_splits=2, embargo_bars=warmup + max_ttl)
    for s in splits:
        gap = min(s.oos_index) - max(s.is_index) - 1
        assert gap == warmup + max_ttl


def test_window_count_is_split_count_not_calendar() -> None:
    # Two different n_bars with the same n_splits yield the same NUMBER of
    # windows — the count is structural, not a function of elapsed time.
    a = walk_forward_splits(n_bars=100, n_splits=4, embargo_bars=3)
    b = walk_forward_splits(n_bars=400, n_splits=4, embargo_bars=3)
    assert len(a) == len(b) == 4


def test_degenerate_too_few_bars_returns_empty() -> None:
    assert walk_forward_splits(n_bars=5, n_splits=3, embargo_bars=10) == ()


def test_indices_within_range() -> None:
    n = 90
    splits = walk_forward_splits(n_bars=n, n_splits=3, embargo_bars=4)
    for s in splits:
        assert all(0 <= i < n for i in s.is_index)
        assert all(0 <= i < n for i in s.oos_index)
