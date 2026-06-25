"""Probability of Backtest Overfitting (PBO/CSCV) + new-strategy admission gate.

PBO — Bailey, Borwein, Lopez de Prado & Zhu (2015), *The Probability of Backtest
Overfitting*. Given a performance matrix ``M`` (rows = trial configs, cols =
backtest paths / sub-periods), CSCV splits the columns into all balanced halves
``(IS, OOS)``. For each split it picks the config that ranks best IS and records
that config's RELATIVE rank OOS (mapped to a logit). PBO = the fraction of splits
whose IS-best config lands at or below the OOS median (logit <= 0) — i.e. the
in-sample winner was a phantom edge that did not generalise. A no-information
matrix (all configs tied every path) lands every IS-best at logit 0 -> PBO 1.0
(the maximally-overfit verdict for a no-edge search; ``admit_strategy`` would
also reject it on the deflated-Sharpe floor).

Admission gate — for ONBOARDING a NEW strategy into the registry: admit only
when the deflated Sharpe (reused verbatim from ``benchmark.statistics``, the
Lopez de Prado trial-deflated PSR) clears a floor AND PBO is below an overfit
ceiling. This is an EXPECTANCY admission test on which strategies ENTER the
search — it gates registry membership, NOT the running bot's sizing. No throttle,
no time-elapsed gate, no dampen of live flow (flow_not_block preserved): a
strategy that passes runs at full aggressive size; one that fails simply never
enters. Pure: no DB, no network, deterministic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Final

__all__ = [
    "DEFAULT_DSR_ADMIT",
    "DEFAULT_PBO_MAX",
    "OverfitVerdict",
    "admit_strategy",
    "pbo",
]

# --- admission thresholds (named — no magic numbers in callers) ------------
# PBO ceiling for admission. Bailey et al. frame PBO as a probability in [0,1];
# 0.5 = a coin-flip generalisation (the IS winner is as likely to lose as win
# OOS). A NEW strategy must do clearly better than chance to enter the search,
# so the admission ceiling is set well below 0.5. Caller-overridable.
DEFAULT_PBO_MAX: Final[float] = 0.30
# Deflated-Sharpe floor for admission. ``deflated_sharpe`` returns the
# trial-adjusted probability (PSR vs the inflated benchmark) that the true Sharpe
# is positive; > 0.5 means edge survives the multiple-testing deflation. The
# admission floor is a one-sided confidence the deflated edge is real. This is
# distinct from (and looser than) the go-live ``gate._PSR_PASS`` (0.95): admission
# decides which candidates are WORTH replaying further, the 3-tier gate decides
# go-live. Caller-overridable.
DEFAULT_DSR_ADMIT: Final[float] = 0.60


@dataclass(frozen=True, slots=True)
class OverfitVerdict:
    """Admission decision + the two evidence scalars and the reason string."""

    admit: bool
    deflated_sharpe: float
    pbo: float
    reason: str


def _rank_argmax(values: list[float]) -> int:
    """Index of the max value (lowest index wins ties — deterministic)."""
    best_i = 0
    best_v = values[0]
    for i in range(1, len(values)):
        if values[i] > best_v:
            best_v, best_i = values[i], i
    return best_i


def _oos_relative_rank(oos_perf: list[float], config: int) -> float:
    """Relative rank ``w`` in (0,1] of ``config`` among ``oos_perf`` (1 = best).

    Ties share the average position so the logit is symmetric around 0.5.
    """
    n = len(oos_perf)
    target = oos_perf[config]
    less = sum(1 for v in oos_perf if v < target)
    equal = sum(1 for v in oos_perf if v == target)
    # Average rank position of the (possibly tied) target, in [1, n].
    avg_pos = less + (equal + 1) / 2.0
    return avg_pos / (n + 1)  # in (0,1), never exactly 0 or 1


def pbo(perf_matrix: list[list[float]]) -> float:
    """Probability of Backtest Overfitting via CSCV.

    ``perf_matrix`` is rows = trial configs, cols = backtest paths (every row
    must have the same length). Returns a probability in [0,1]; higher = more
    overfit. Degenerate inputs (< 2 configs, < 2 paths, or an odd column count
    that cannot be split into equal halves with at least one valid split) return
    ``0.5`` (no evidence either way).
    """
    n_configs = len(perf_matrix)
    if n_configs < 2:
        return 0.5
    n_paths = len(perf_matrix[0])
    if n_paths < 2 or any(len(row) != n_paths for row in perf_matrix):
        return 0.5
    half = n_paths // 2
    if half < 1:
        return 0.5
    # CSCV: enumerate every way to choose ``half`` of the columns as IS; the
    # complementary columns are OOS. Each (IS, OOS) pair is one symmetric split.
    cols = range(n_paths)
    logits: list[float] = []
    for is_cols in combinations(cols, half):
        is_set = set(is_cols)
        oos_cols = [c for c in cols if c not in is_set]
        # IS performance per config = mean over the IS columns.
        is_perf = [
            sum(row[c] for c in is_cols) / half for row in perf_matrix
        ]
        oos_perf = [
            sum(row[c] for c in oos_cols) / len(oos_cols) for row in perf_matrix
        ]
        best = _rank_argmax(is_perf)  # the config chosen in-sample
        w = _oos_relative_rank(oos_perf, best)  # its OOS relative rank in (0,1)
        # Logit of the OOS rank; < 0 <=> below-median OOS (overfit instance).
        logits.append(math.log(w / (1.0 - w)))
    if not logits:
        return 0.5
    overfit = sum(1 for lam in logits if lam <= 0.0)
    return overfit / len(logits)


def admit_strategy(
    *,
    deflated_sharpe: float,
    pbo_value: float,
    dsr_min: float = DEFAULT_DSR_ADMIT,
    pbo_max: float = DEFAULT_PBO_MAX,
) -> OverfitVerdict:
    """Admit a NEW strategy iff its deflated Sharpe clears ``dsr_min`` AND its
    PBO is at or below ``pbo_max``.

    Boundary is inclusive on both rails (``>= dsr_min`` and ``<= pbo_max``) —
    documented so an exactly-at-threshold candidate is admitted. Phantom edge
    (high in-sample Sharpe but PBO above the ceiling) is rejected; a candidate
    whose deflated edge never cleared the floor is rejected even at PBO 0. This
    decides registry membership only — never a live-sizing throttle.
    """
    dsr_ok = deflated_sharpe >= dsr_min
    pbo_ok = pbo_value <= pbo_max
    if dsr_ok and pbo_ok:
        reason = (
            f"admit: deflated_sharpe={deflated_sharpe:.3f} >= {dsr_min:.2f} "
            f"and PBO={pbo_value:.3f} <= {pbo_max:.2f}"
        )
        return OverfitVerdict(True, deflated_sharpe, pbo_value, reason)
    parts: list[str] = []
    if not dsr_ok:
        parts.append(
            f"deflated_sharpe={deflated_sharpe:.3f} < floor {dsr_min:.2f} "
            "(no significant edge after trial deflation)"
        )
    if not pbo_ok:
        parts.append(
            f"PBO={pbo_value:.3f} > ceiling {pbo_max:.2f} "
            "(in-sample edge does not generalise — phantom)"
        )
    return OverfitVerdict(False, deflated_sharpe, pbo_value, "reject: " + "; ".join(parts))
