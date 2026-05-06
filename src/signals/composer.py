"""CompositeScorer — 5-factor weighted signal composition (Phase 27, composer-lite).

Pure function (P6). No I/O, deterministic.

5 factors (all in [0, 1]):
  strength      — raw signal strength (e.g. RSI distance, BB penetration)
  momentum      — short-term price momentum direction agreement
  volume        — volume confirmation of the move
  regime_fit    — how well current regime fits the strategy
  microstructure — orderbook / spread / liquidity quality

None value for a factor = "not available" — that factor is excluded from the
weighted average and its weight is redistributed proportionally to present factors.

References:
- ADR-007: Polaris paper sizing baseline
- Codex Phase 27 debate consensus: 5-factor composer-lite (no CFD pollution)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Canonical factor names — all 5 must be present in any custom weight dict.
FACTOR_NAMES: tuple[str, ...] = (
    "strength", "momentum", "volume", "regime_fit", "microstructure"
)

# Default weights — sum to 1.0.
# Rationale: strength + momentum carry most directional conviction;
# volume confirms; regime_fit and microstructure are secondary filters.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "strength":      0.30,
    "momentum":      0.25,
    "volume":        0.20,
    "regime_fit":    0.15,
    "microstructure": 0.10,
}


@dataclass(frozen=True, slots=True)
class FactorInputs:
    """All 5 factor values for one signal evaluation.

    Each factor must be in [0.0, 1.0] or None (not available).
    At least one factor must be non-None for scoring.

    Invariants checked in __post_init__:
    - Each non-None value in [0.0, 1.0]
    """

    strength: Optional[float]
    momentum: Optional[float]
    volume: Optional[float]
    regime_fit: Optional[float]
    microstructure: Optional[float]

    def __post_init__(self) -> None:
        for name in FACTOR_NAMES:
            val = getattr(self, name)
            if val is not None and not (0.0 <= val <= 1.0):
                raise ValueError(
                    f"Factor '{name}' must be in [0.0, 1.0] or None, got {val}"
                )


class CompositeScorer:
    """Weighted combination of 5 signal factors → composite score in [0, 1].

    Pure: no I/O, no side effects. Deterministic.

    Usage:
        scorer = CompositeScorer()                     # default weights
        scorer = CompositeScorer(weights={...})        # custom weights
        score = scorer.score(FactorInputs(...))        # → float in [0, 1]
    """

    def __init__(self, weights: Optional[dict[str, float]] = None) -> None:
        if weights is None:
            self._weights = dict(_DEFAULT_WEIGHTS)
        else:
            self._weights = self._validate_weights(weights)

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    def score(self, inputs: FactorInputs) -> float:
        """Compute composite score as weighted average of available factors.

        None factors are excluded; remaining weights are renormalized to sum to 1.

        Returns:
            float in [0.0, 1.0]

        Raises:
            ValueError: all factors are None (no signal available)
        """
        present: list[tuple[str, float]] = []
        for name in FACTOR_NAMES:
            val = getattr(inputs, name)
            if val is not None:
                present.append((name, val))

        if not present:
            raise ValueError("All factors are None — cannot compute composite score")

        # Renormalize weights for present factors only
        raw_weight_sum = sum(self._weights[name] for name, _ in present)
        if raw_weight_sum <= 0.0:
            # Edge case: all present factors have weight=0 (custom weights scenario)
            # Fall back to uniform average
            return sum(val for _, val in present) / len(present)

        weighted_sum = sum(
            val * (self._weights[name] / raw_weight_sum)
            for name, val in present
        )
        # Clamp to [0, 1] for float precision safety
        return max(0.0, min(1.0, weighted_sum))

    @staticmethod
    def _validate_weights(weights: dict[str, float]) -> dict[str, float]:
        """Validate custom weight dict.

        Rules:
        - Must contain all 5 FACTOR_NAMES as keys
        - Values must be >= 0
        - Must sum to 1.0 (±1e-9 tolerance)
        """
        missing = set(FACTOR_NAMES) - set(weights.keys())
        if missing:
            raise ValueError(f"Custom weights missing factors: {sorted(missing)}")

        for name, w in weights.items():
            if w < 0:
                raise ValueError(f"Weight for '{name}' must be >= 0, got {w}")

        total = sum(weights[name] for name in FACTOR_NAMES)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"Custom weights must sum to 1.0, got {total:.10f}"
            )
        return dict(weights)


def composite_score(inputs: FactorInputs, weights: Optional[dict[str, float]] = None) -> float:
    """Functional alias for CompositeScorer.score with default or custom weights.

    Pure function. No I/O.

    Args:
        inputs: FactorInputs with 5 factors (each in [0,1] or None)
        weights: optional custom weight dict (must sum to 1.0)

    Returns:
        float in [0.0, 1.0]
    """
    return CompositeScorer(weights=weights).score(inputs)
