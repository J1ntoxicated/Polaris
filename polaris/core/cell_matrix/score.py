"""Layer 4 — pure score helpers (EWMA decay, T11 score, warmup shrinkage).

Spec source: vault/30_components/layer-4-cell-matrix.md (Q2, Q4, Q5).
"""

from __future__ import annotations

import math
from typing import Final

from polaris.core.cell_matrix.schema import (
    CELL_BASELINE_N,
    CELL_DECAY_HALF_LIFE_SEC,
    CELL_MIN_LIVE_N,
    CELL_SHRINKAGE_N,
)

__all__ = [
    "apply_exponential_decay",
    "compute_avg_pnl_r",
    "compute_cell_score",
    "decay_factor",
    "resolve_effective_score",
]

_LN2: Final[float] = math.log(2.0)


def decay_factor(*, elapsed_sec: float, half_life_sec: float = CELL_DECAY_HALF_LIFE_SEC) -> float:
    """Return ``exp(-elapsed × ln 2 / half_life)``.

    ``elapsed_sec < 0`` clamps to 0 so clock skew never inflates state.
    ``half_life_sec <= 0`` raises — silent fallback would mask config rot.
    """
    if half_life_sec <= 0.0:
        raise ValueError("half_life_sec must be > 0")
    if not math.isfinite(elapsed_sec):
        raise ValueError("elapsed_sec must be finite")
    if elapsed_sec <= 0.0:
        return 1.0
    return math.exp(-elapsed_sec * _LN2 / half_life_sec)


def apply_exponential_decay(
    value: float,
    *,
    elapsed_sec: float,
    half_life_sec: float = CELL_DECAY_HALF_LIFE_SEC,
) -> float:
    """Decay a scalar by EWMA factor."""
    return value * decay_factor(elapsed_sec=elapsed_sec, half_life_sec=half_life_sec)


def compute_avg_pnl_r(*, pnl_r_sum_eff: float, n_eff: float) -> float:
    """``avg_pnl_r = pnl_r_sum_eff / n_eff`` with safe zero handling."""
    if n_eff <= 0.0:
        return 0.0
    return pnl_r_sum_eff / n_eff


def compute_cell_score(
    *,
    avg_pnl_r: float,
    n_eff: float,
    baseline_n: float = CELL_BASELINE_N,
) -> float:
    """T11 cell-confidence score.

    ``score = avg_pnl_r × √n_eff / baseline_n``.

    Floor 0 not applied — losing cells keep their negative score so the
    bottom-quartile router can find them.
    """
    if baseline_n <= 0.0:
        raise ValueError("baseline_n must be > 0")
    if n_eff <= 0.0:
        return 0.0
    if not (math.isfinite(avg_pnl_r) and math.isfinite(n_eff)):
        return 0.0
    return avg_pnl_r * math.sqrt(n_eff) / baseline_n


def resolve_effective_score(
    *,
    cell_score: float,
    cell_n_eff: float,
    parent3_score: float | None,
    parent2_score: float | None,
    shrinkage_n: float = CELL_SHRINKAGE_N,
    min_live_n: float = CELL_MIN_LIVE_N,
) -> float:
    """Apply warmup shrinkage to a cell score.

    | n_eff range          | result                                            |
    |----------------------|---------------------------------------------------|
    | n_eff < min_live_n   | 0.0 (caller must route ×1.0 neutral on this)      |
    | min_live_n ≤ n < 20  | blend ``(n/20)·cell + ((20-n)/20)·parent``        |
    | n ≥ shrinkage_n      | cell_score unchanged                              |

    Parent fallback order: parent3 → parent2 → 0.0. Negative scores preserved.
    """
    if shrinkage_n <= 0.0:
        raise ValueError("shrinkage_n must be > 0")
    if cell_n_eff < min_live_n:
        return 0.0
    if cell_n_eff >= shrinkage_n:
        return cell_score
    if parent3_score is not None:
        parent = parent3_score
    elif parent2_score is not None:
        parent = parent2_score
    else:
        parent = 0.0
    weight_cell = cell_n_eff / shrinkage_n
    weight_parent = 1.0 - weight_cell
    return weight_cell * cell_score + weight_parent * parent
