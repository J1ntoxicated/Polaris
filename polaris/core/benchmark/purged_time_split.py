"""Purged/embargo TIME-axis split generator (pure, no I/O).

Adapts the bar-index embargo pattern from ``walk_forward.py`` / ``cpcv.py`` to
observations that do not sit on a uniform bar grid — each observation carries
its own ``[start_ts, end_ts)`` interval (frontgate-scan item #10: a
``meta_labels`` row's inferred [entry_ts, created_ts) trade-open interval; see
``core/learners/meta_label_report.py``). Same purge concept as the bar-index
splitters: an observation whose interval falls within ``embargo_sec`` of a
split boundary is DROPPED from both sides — a trade whose HOLDING PERIOD
straddles the boundary would otherwise leak information across it either way
(a future 2nd-stage act/skip model's CV harness must not see it in both IS
and OOS). No new algorithm — the equal-count block partition + trailing-embargo-
gap contract mirrors ``walk_forward_splits`` exactly; only the purge PREDICATE
changes from "index distance" to "interval overlap".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["PurgedTimeSplit", "TimedObservation", "purged_time_splits"]


@dataclass(frozen=True, slots=True)
class TimedObservation:
    """One observation's identity + its ``[start_ts, end_ts)`` interval."""

    obs_id: str
    start_ts: int
    end_ts: int


@dataclass(frozen=True, slots=True)
class PurgedTimeSplit:
    """One purged fold: in-sample + out-of-sample observation ids."""

    split_id: int
    is_ids: tuple[str, ...]
    oos_ids: tuple[str, ...]


def purged_time_splits(
    observations: Sequence[TimedObservation],
    *,
    n_splits: int,
    embargo_sec: int,
    anchored: bool = True,
) -> tuple[PurgedTimeSplit, ...]:
    """Generate ``n_splits`` purged walk-forward folds over a TIME axis.

    Observations are sorted by ``end_ts`` (when the label is actually KNOWN —
    the close) and divided into ``n_splits + 1`` equal-COUNT blocks: one IS
    seed block plus ``n_splits`` marching OOS blocks (the same equal-block
    partition ``walk_forward_splits`` uses). The split boundary is the
    boundary OOS block's first observation's ``start_ts``; any observation in
    either candidate block whose ``[start_ts, end_ts)`` interval overlaps
    ``[boundary_ts - embargo_sec, boundary_ts + embargo_sec]`` is purged from
    BOTH sides. ``anchored`` grows the IS window from the first block each
    fold; otherwise IS rolls with a fixed block count. Returns ``()`` on any
    infeasible request (too few observations, or a fold whose purge empties
    one side) — honest degenerate case, no silent shrink.
    """
    n = len(observations)
    if n_splits < 1 or n < 1 or embargo_sec < 0:
        return ()
    ordered = sorted(observations, key=lambda o: o.end_ts)
    oos_block = n // (n_splits + 1)
    if oos_block < 1:
        return ()
    is_seed = n - n_splits * oos_block
    if is_seed < 1:
        return ()
    splits: list[PurgedTimeSplit] = []
    for k in range(n_splits):
        is_end_idx = is_seed + k * oos_block
        oos_start_idx = is_end_idx
        oos_end_idx = oos_start_idx + oos_block
        if oos_end_idx > n:
            break
        is_start_idx = 0 if anchored else k * oos_block
        is_candidates = ordered[is_start_idx:is_end_idx]
        oos_candidates = ordered[oos_start_idx:oos_end_idx]
        boundary_ts = ordered[oos_start_idx].start_ts
        embargo_lo = boundary_ts - embargo_sec
        embargo_hi = boundary_ts + embargo_sec
        is_ids = tuple(
            o.obs_id for o in is_candidates
            if o.end_ts <= embargo_lo or o.start_ts >= embargo_hi
        )
        oos_ids = tuple(
            o.obs_id for o in oos_candidates
            if o.end_ts <= embargo_lo or o.start_ts >= embargo_hi
        )
        if not is_ids or not oos_ids:
            break
        splits.append(PurgedTimeSplit(split_id=k, is_ids=is_ids, oos_ids=oos_ids))
    return tuple(splits)
