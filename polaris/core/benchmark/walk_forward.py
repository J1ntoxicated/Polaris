"""Purged/embargo walk-forward split generator over BAR INDEX (pure, no I/O).

Spec: ``.claude/plans/p1_replay_benchmark_harness_2026-05-31.md`` §Walk-forward.
Anti-overfit, **NOT a calendar gate**: splits are a WINDOW *count* over bar
indices, never "weeks elapsed". Params are FROZEN, so the in-sample (IS) window
only selects which cell/strategy is "live"; the out-of-sample (OOS) window
measures held-out edge. An ``embargo_bars = warmup_bars + max_ttl`` gap is
dropped between IS and OOS so no trade straddles the boundary (leakage block).

The IS/OOS *spread* (bot Sharpe IS − OOS) is the overfit flag the gate reports —
a large positive gap means the IS selection did not generalise.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "WalkForwardSplit",
    "walk_forward_splits",
]


@dataclass(frozen=True, slots=True)
class WalkForwardSplit:
    """One walk-forward fold: in-sample + (embargoed) out-of-sample bar indices.

    ``is_index`` / ``oos_index`` are sorted tuples of bar positions (0-based into
    the shared bar sequence). The ``embargo_bars`` between ``max(is_index)`` and
    ``min(oos_index)`` are dropped from both (purge). ``split_id`` is the 0-based
    fold ordinal (a window count, not a date).
    """

    split_id: int
    is_index: tuple[int, ...]
    oos_index: tuple[int, ...]


def walk_forward_splits(
    *,
    n_bars: int,
    n_splits: int,
    embargo_bars: int,
    anchored: bool = True,
) -> tuple[WalkForwardSplit, ...]:
    """Generate ``n_splits`` purged walk-forward folds over ``n_bars`` indices.

    The usable horizon (after one terminal OOS block + the embargo gaps) is
    divided into ``n_splits`` equal OOS blocks that march forward. ``anchored``
    grows the IS window from bar 0 each fold; otherwise the IS window rolls with
    a fixed length. Returns ``()`` when the horizon cannot host even one fold
    with the requested embargo (honest degenerate case — no silent shrink).
    """
    if n_splits < 1 or n_bars < 1 or embargo_bars < 0:
        return ()
    # Each fold consumes: >=1 IS bar + embargo + >=1 OOS bar. Need enough room
    # for n_splits OOS blocks plus the IS seed and the embargo gaps.
    oos_block = (n_bars - embargo_bars) // (n_splits + 1)
    if oos_block < 1:
        return ()
    is_seed = n_bars - n_splits * (oos_block + embargo_bars)
    if is_seed < 1:
        return ()
    splits: list[WalkForwardSplit] = []
    for k in range(n_splits):
        # IS end (exclusive) grows by one OOS block + embargo each fold.
        is_end = is_seed + k * (oos_block + embargo_bars)
        oos_start = is_end + embargo_bars
        oos_end = oos_start + oos_block
        if oos_end > n_bars:
            break
        is_start = 0 if anchored else k * (oos_block + embargo_bars)
        is_idx = tuple(range(is_start, is_end))
        oos_idx = tuple(range(oos_start, oos_end))
        if not is_idx or not oos_idx:
            break
        splits.append(WalkForwardSplit(split_id=k, is_index=is_idx, oos_index=oos_idx))
    return tuple(splits)
