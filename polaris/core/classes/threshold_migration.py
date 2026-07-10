"""fee-split v0 (group GROSS) — threshold migration (ECDF percentile
preservation + isotonic order enforcement).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Pure
math — no DB, no I/O. Spec source:
vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 item 5:

    "임계 이관 = per-transition ECDF percentile 보존 + isotonic 순서 강제 +
    섀도우 120 청산 병행(행동 발산 <=15%*tier <=10%*전이율 <=7.5pp) 후 flip."

The classifier's existing thresholds (e.g. ``transition.py``'s Schmitt
-1.0/-3.0/-4.0, PROVE stagnation +2.0) live in the OLD fee-normalized
score_contrib scale. Moving the survival FSM onto the NEW gross-edge scale
(v1 scope) must preserve each threshold's RELATIVE STANDING in its own
population — a threshold that used to sit at the old population's 15th
percentile should sit at the new population's 15th percentile too, not at
some arbitrarily different rank. ``migrate_thresholds`` does exactly that:
percentile-rank each old threshold against the OLD population, then map that
percentile onto the NEW population's value at the same rank.

Percentile-mapping alone does not guarantee the migrated thresholds keep
their ORIGINAL ORDER (independent percentile lookups of nearby old
thresholds can cross under a noisy/discontinuous ECDF) — ``enforce_isotonic``
repairs that via pool-adjacent-violators (PAVA), the standard isotonic-
regression algorithm, keeping migrated thresholds monotonic in the same
direction the caller's ORIGINAL threshold sequence was.
"""

from __future__ import annotations

__all__ = [
    "ecdf_percentile",
    "enforce_isotonic",
    "migrate_thresholds",
    "value_at_percentile",
]


def ecdf_percentile(value: float, population: list[float]) -> float:
    """Empirical-CDF percentile rank of ``value`` within ``population``, in
    [0, 1] — the fraction of the population at or below ``value``.

    Empty population -> 0.5 (no information -> the neutral midpoint, never a
    crash / never a silently-biased 0.0 or 1.0).
    """
    if not population:
        return 0.5
    n = len(population)
    n_at_or_below = sum(1 for p in population if p <= value)
    return n_at_or_below / n


def value_at_percentile(population: list[float], pct: float) -> float:
    """Inverse of :func:`ecdf_percentile` — the population value at rank
    ``pct`` (in [0, 1]) via nearest-rank on the sorted population.

    Empty population -> 0.0 (nothing to map onto; caller's own fallback
    should apply — this is a degenerate/defensive case, not the expected
    path in production use where the new population has accumulated
    at least the N_eff floor's worth of samples).
    """
    if not population:
        return 0.0
    ordered = sorted(population)
    pct = min(max(pct, 0.0), 1.0)
    idx = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[idx]


def migrate_thresholds(
    old_thresholds: list[float],
    *,
    old_population: list[float],
    new_population: list[float],
) -> list[float]:
    """Percentile-preserving migration of ``old_thresholds`` from
    ``old_population``'s scale onto ``new_population``'s scale, then
    isotonic-order-repaired against the ORIGINAL threshold sequence's
    direction (see :func:`enforce_isotonic`).
    """
    percentiles = [ecdf_percentile(t, old_population) for t in old_thresholds]
    migrated = [value_at_percentile(new_population, p) for p in percentiles]
    return enforce_isotonic(migrated, reference=old_thresholds)


def enforce_isotonic(values: list[float], *, reference: list[float]) -> list[float]:
    """Repair ``values`` into monotonic order matching ``reference``'s
    direction via pool-adjacent-violators (PAVA) — the standard isotonic-
    regression algorithm (L2-optimal monotonic fit).

    Direction is inferred from ``reference`` (non-decreasing if
    ``reference`` is non-decreasing, else non-increasing) so a caller's
    threshold sequence stays faithful to ITS OWN intended ordering (e.g.
    transition.py's Schmitt thresholds are naturally increasing:
    BENCH-partial(-4.0) < BENCH-full(-3.0) < PROVE(-1.0)).
    """
    if len(values) <= 1:
        return list(values)
    non_decreasing = _is_non_decreasing(reference)
    working = list(values) if non_decreasing else [-v for v in values]
    pooled = _pava_non_decreasing(working)
    return pooled if non_decreasing else [-v for v in pooled]


def _is_non_decreasing(seq: list[float]) -> bool:
    return all(a <= b for a, b in zip(seq, seq[1:], strict=False))


def _pava_non_decreasing(values: list[float]) -> list[float]:
    """Pool-adjacent-violators for a non-decreasing fit. Each pooled block
    tracks (mean, weight=count) so merges average correctly."""
    blocks: list[list[float]] = [[v, 1.0] for v in values]  # [mean, weight]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0]:
            w_total = blocks[i][1] + blocks[i + 1][1]
            mean = (blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]) / w_total
            blocks[i] = [mean, w_total]
            del blocks[i + 1]
            i = max(0, i - 1)
        else:
            i += 1
    out: list[float] = []
    for mean, weight in blocks:
        out.extend([mean] * int(weight))
    return out
