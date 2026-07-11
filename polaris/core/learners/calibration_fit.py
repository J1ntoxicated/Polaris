"""Offline Platt/PAV calibration fitting utilities (frontgate-scan item #4, G5).

Pure, dependency-0 (stdlib ``math`` only). NEVER imported by the live
sizing/gating pipeline — fitting is an OFFLINE analysis step (fed by
``calibration_shadow.py``'s ``calibration_pairs`` collection, consumed by
/debate review or a future promotion decision), not a runtime scalar. No
application path exists here on purpose (roadmap item #4: "적용 없이
Brier/커버리지 리포트만").

- :func:`platt_fit` / :func:`platt_apply` — 2-parameter logistic regression
  (A, B) mapping a raw predicted probability to a calibrated one (Platt 1999,
  Newton's method per Lin/Lin/Weng 2007's numerically-safe target values).
- :func:`pav_fit` / :func:`pav_apply` — Pool-Adjacent-Violators isotonic
  regression (a monotone step function, no parametric form).
- :func:`brier_score` — mean squared error of predicted vs realized 0/1.
- :func:`coverage_report` — decile reliability-diagram data (predicted mean
  vs realized win rate per bin).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "CoverageBin",
    "PavFit",
    "brier_score",
    "coverage_report",
    "pav_apply",
    "pav_fit",
    "platt_apply",
    "platt_fit",
]


# ---------------------------------------------------------------------------
# Platt scaling — 2-parameter logistic regression via Newton's method
# ---------------------------------------------------------------------------


def platt_fit(
    scores: list[float], labels: list[int], *, max_iter: int = 100
) -> tuple[float, float]:
    """Fit ``(A, B)`` such that ``sigmoid(A*score + B) ~= P(y=1|score)``.

    Newton's method on the safe (non-overfitting) target values from Platt
    (1999) / Lin, Lin & Weng (2007): ``hi_target`` for positive labels,
    ``lo_target`` for negative, both shrunk away from the {0,1} extremes.
    Returns ``(0.0, 0.0)`` (identity-ish — sigmoid(0)=0.5) on a degenerate
    input (empty, or a single class only — no separating boundary exists).
    """
    n = len(scores)
    if n == 0 or len(labels) != n:
        return 0.0, 0.0
    n_pos = sum(1 for y in labels if y > 0)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.0, 0.0
    hi_target = (n_pos + 1.0) / (n_pos + 2.0)
    lo_target = 1.0 / (n_neg + 2.0)
    targets = [hi_target if y > 0 else lo_target for y in labels]
    a = 0.0
    b = math.log((n_neg + 1.0) / (n_pos + 1.0))
    for _ in range(max_iter):
        h11 = h22 = 1e-12
        h21 = g1 = g2 = 0.0
        for f, t in zip(scores, targets, strict=True):
            fapb = f * a + b
            if fapb >= 0.0:
                p = math.exp(-fapb) / (1.0 + math.exp(-fapb))
            else:
                p = 1.0 / (1.0 + math.exp(fapb))
            d2 = p * (1.0 - p)
            d1 = t - p
            h11 += f * f * d2
            h22 += d2
            h21 += f * d2
            g1 += f * d1
            g2 += d1
        if abs(g1) < 1e-5 and abs(g2) < 1e-5:
            break
        det = h11 * h22 - h21 * h21
        if det == 0.0:
            break
        da = -(h22 * g1 - h21 * g2) / det
        db = -(-h21 * g1 + h11 * g2) / det
        a += da
        b += db
    return a, b


def platt_apply(score: float, a: float, b: float) -> float:
    """Map a raw ``score`` to a calibrated probability via the fitted (A, B)."""
    fapb = a * score + b
    if fapb >= 0.0:
        return math.exp(-fapb) / (1.0 + math.exp(-fapb))
    return 1.0 / (1.0 + math.exp(fapb))


# ---------------------------------------------------------------------------
# PAV — Pool-Adjacent-Violators isotonic regression
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PavFit:
    """Monotone step function: sorted ``(x_upper_bound, calibrated_mean)`` pairs."""

    steps: tuple[tuple[float, float], ...]


def pav_fit(scores: list[float], labels: list[int]) -> PavFit:
    """Pool-adjacent-violators isotonic regression of ``labels`` on ``scores``.

    Standard PAV: sort by score, then greedily merge ("pool") adjacent blocks
    whenever a later block's mean would violate monotonicity with the block
    before it. Returns the merged blocks' ``(x_upper_bound, mean)`` — a
    monotone non-decreasing step function of score.
    """
    if not scores or len(scores) != len(labels):
        return PavFit(steps=())
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ysum: list[float] = []
    w: list[float] = []
    x_hi: list[float] = []
    for i in order:
        ysum.append(float(labels[i]))
        w.append(1.0)
        x_hi.append(scores[i])
        while len(ysum) > 1 and (ysum[-2] / w[-2]) > (ysum[-1] / w[-1]):
            y2, w2, xh2 = ysum.pop(), w.pop(), x_hi.pop()
            y1, w1, _xh1 = ysum.pop(), w.pop(), x_hi.pop()
            ysum.append(y1 + y2)
            w.append(w1 + w2)
            x_hi.append(xh2)
    steps = tuple((x_hi[i], ysum[i] / w[i]) for i in range(len(ysum)))
    return PavFit(steps=steps)


def pav_apply(score: float, fit: PavFit) -> float:
    """Look up the calibrated probability for ``score`` from a fitted ``PavFit``.

    Below the first step's bound → that step's mean (constant extrapolation);
    above the last → the last step's mean. ``0.5`` (uninformed) when the fit
    is empty.
    """
    if not fit.steps:
        return 0.5
    for x_hi, mean in fit.steps:
        if score <= x_hi:
            return mean
    return fit.steps[-1][1]


# ---------------------------------------------------------------------------
# Evaluation — Brier score + decile coverage report
# ---------------------------------------------------------------------------


def brier_score(predicted: list[float], labels: list[int]) -> float:
    """Mean squared error of ``predicted`` probabilities vs realized 0/1 labels.

    Lower is better; ``0.0`` = perfect, ``0.25`` = the uninformed all-0.5
    baseline on a balanced set. Returns ``0.0`` on an empty input (nothing to
    score — never a garbage NaN/inf).
    """
    n = len(predicted)
    if n == 0 or len(labels) != n:
        return 0.0
    return sum(
        (p - y) ** 2 for p, y in zip(predicted, labels, strict=True)
    ) / n


@dataclass(frozen=True, slots=True)
class CoverageBin:
    """One decile bin's reliability-diagram data."""

    bin_index: int
    n: int
    mean_predicted: float
    realized_win_rate: float


def coverage_report(
    predicted: list[float], labels: list[int], *, n_bins: int = 10
) -> tuple[CoverageBin, ...]:
    """Bin ``predicted`` into ``n_bins`` equal-COUNT deciles (sorted by score)
    and compare each bin's mean predicted probability against its realized
    win rate — the standard reliability-diagram table. A well-calibrated
    model has ``mean_predicted ~= realized_win_rate`` in every bin. Returns
    fewer than ``n_bins`` bins when there are too few pairs to fill all of
    them (honest — no fabricated empty bins).
    """
    n = len(predicted)
    if n == 0 or len(labels) != n or n_bins < 1:
        return ()
    order = sorted(range(n), key=lambda i: predicted[i])
    bin_size = max(1, n // n_bins)
    bins: list[CoverageBin] = []
    start = 0
    bin_index = 0
    while start < n:
        end = min(start + bin_size, n)
        # Last bin absorbs any remainder so every pair is counted exactly once.
        if n - end < bin_size:
            end = n
        idx = order[start:end]
        count = len(idx)
        mean_pred = sum(predicted[i] for i in idx) / count
        win_rate = sum(labels[i] for i in idx) / count
        bins.append(
            CoverageBin(
                bin_index=bin_index, n=count, mean_predicted=mean_pred,
                realized_win_rate=win_rate,
            )
        )
        start = end
        bin_index += 1
    return tuple(bins)
