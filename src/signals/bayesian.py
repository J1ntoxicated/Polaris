"""Bayesian confidence calibration — lite (Phase 27).

Pure function (P6). No I/O, deterministic.

NOT a quality gate. Input: lifetime win_rate × composite_score → calibrated_confidence.

Design (Codex Phase 27 consensus):
  calibrated = blend(prior, evidence)
  - prior = composite_score (current signal conviction)
  - evidence = win_rate (historical edge)
  - blend weight: sigmoid on n_trades (cold start → prior-dominant; warm → evidence-dominant)
  - result clamped to [0, 1]

Monotone invariants:
  - increasing win_rate → calibrated >= previous (with same score)
  - increasing composite_score → calibrated >= previous (with same win_rate)

References:
- ADR-007: Polaris paper sizing baseline
- INSIGHT-032: Phase 2j AI sizing revival
- Codex Phase 27 debate: bayesian lite (not full BayesianPredictor port)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Cold-start blend parameter: at n_trades=0 → 100% prior (composite_score).
# At n_trades → ∞ → evidence_weight → MAX_EVIDENCE_WEIGHT.
# Sigmoid midpoint: n_trades=30 gives ~50% evidence weight.
_SIGMOID_MIDPOINT = 30.0
_SIGMOID_STEEPNESS = 0.1
_MAX_EVIDENCE_WEIGHT = 0.85   # max fraction attributable to win_rate evidence


@dataclass(frozen=True, slots=True)
class CalibrationInput:
    """Inputs for Bayesian confidence calibration.

    Invariants:
    - win_rate in [0.0, 1.0]
    - composite_score in [0.0, 1.0]
    - n_trades >= 0
    """

    win_rate: float       # lifetime win fraction for this strategy/ticker
    composite_score: float # output of CompositeScorer.score (in [0, 1])
    n_trades: int          # number of closed trades in history

    def __post_init__(self) -> None:
        if not (0.0 <= self.win_rate <= 1.0):
            raise ValueError(f"win_rate must be in [0.0, 1.0], got {self.win_rate}")
        if not (0.0 <= self.composite_score <= 1.0):
            raise ValueError(f"composite_score must be in [0.0, 1.0], got {self.composite_score}")
        if self.n_trades < 0:
            raise ValueError(f"n_trades must be >= 0, got {self.n_trades}")


def _evidence_weight(n_trades: int) -> float:
    """Sigmoid-shaped evidence weight in [0, MAX_EVIDENCE_WEIGHT].

    n_trades=0  → 0.0  (all prior)
    n_trades=30 → ~0.5 × MAX_EVIDENCE_WEIGHT
    n_trades=∞  → MAX_EVIDENCE_WEIGHT
    """
    # Sigmoid: 1 / (1 + exp(-k*(n - m))) — shifted so at n=0 → 0.0
    # Standard sigmoid gives 0.5 at n=midpoint; we shift to get 0.0 at n=0.
    sig_at_zero = 1.0 / (1.0 + math.exp(_SIGMOID_STEEPNESS * _SIGMOID_MIDPOINT))
    sig_at_n = 1.0 / (1.0 + math.exp(-_SIGMOID_STEEPNESS * (n_trades - _SIGMOID_MIDPOINT)))
    # Normalize: 0 at n=0, 1 at n→∞
    sig_max = 1.0 - sig_at_zero
    if sig_max <= 0:
        return 0.0
    normalized = (sig_at_n - sig_at_zero) / sig_max
    return max(0.0, min(1.0, normalized)) * _MAX_EVIDENCE_WEIGHT


def calibrate_confidence(inputs: CalibrationInput) -> float:
    """Bayesian-lite confidence calibration.

    Blends composite_score (prior) with win_rate (evidence) using sigmoid
    weight based on n_trades. More trades → win_rate dominates.

    Monotone guarantees:
    - More win_rate → higher calibrated (all else equal)
    - Higher composite_score → higher calibrated (all else equal)

    Returns:
        float in [0.0, 1.0]
    """
    ev_weight = _evidence_weight(inputs.n_trades)
    prior_weight = 1.0 - ev_weight

    calibrated = prior_weight * inputs.composite_score + ev_weight * inputs.win_rate
    return max(0.0, min(1.0, calibrated))
