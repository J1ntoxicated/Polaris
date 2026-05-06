"""signals package — composite signal scoring (Phase 27).

Modules:
- composer: CompositeScorer — 5-factor weighted average (pure, P6)
- bayesian: confidence calibration — lifetime win_rate × score (pure, P6)
"""
from src.signals.composer import CompositeScorer, FactorInputs, composite_score
from src.signals.bayesian import CalibrationInput, calibrate_confidence

__all__ = [
    "CompositeScorer",
    "FactorInputs",
    "composite_score",
    "CalibrationInput",
    "calibrate_confidence",
]
