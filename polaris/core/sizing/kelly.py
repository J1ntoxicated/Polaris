"""Layer 3 — Kelly fractional + Cold Start CS-3 boundary.

Spec source:
- vault/30_components/layer-3-sizing-risk.md (Q2 cold start)
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (Cold Start CS-3)

Rules (P0):
- ``n_closed < 20`` → CS-3 active: Kelly off, single cap = 6% (default) / 7% (amp on)
- ``n_closed >= 20`` → Kelly on, k=0.5 fractional, single cap = 8%/9%
- Kelly p/q clamp: any non-finite or out-of-range value collapses to CS-3 default.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from polaris.core.sizing.schema import (
    CS3_N_THRESHOLD,
    CS3_SINGLE_AMPLIFIED_PCT,
    CS3_SINGLE_DEFAULT_PCT,
    KELLY_FRACTION_K,
    SINGLE_TRADE_AMPLIFIED_PCT,
    SINGLE_TRADE_DEFAULT_PCT,
)

__all__ = [
    "KellyDecision",
    "is_cold_start",
    "kelly_fraction",
    "kelly_or_cold_start",
    "single_trade_cap_pct",
]


@dataclass(frozen=True, slots=True)
class KellyDecision:
    """Output of :func:`kelly_or_cold_start`."""

    cold_start: bool
    kelly_fraction: float  # 0.0 if cold-start
    single_cap_pct: float  # the single-trade cap applied this decision


def is_cold_start(n_closed: int, *, threshold: int = CS3_N_THRESHOLD) -> bool:
    """``True`` when the strategy is below the CS-3 trade-count threshold."""
    return int(n_closed) < int(threshold)


def kelly_fraction(p: float, q: float, *, k: float = KELLY_FRACTION_K) -> float:
    """Fractional Kelly for binary win/loss with R-multiple ratio.

    Standard formula: ``f* = p - q × (loss/win)``. Here we operate on
    R-normalized PnL, so the loss/win ratio collapses to 1 → ``f* = p - q``.

    Returns ``max(0, k × f*)``. Negative edge → 0 (no bet).

    All non-finite or out-of-range inputs return 0 (caller falls back to
    Cold-Start cap).
    """
    if not (math.isfinite(p) and math.isfinite(q) and math.isfinite(k)):
        return 0.0
    if p < 0.0 or p > 1.0 or q < 0.0 or q > 1.0:
        return 0.0
    edge = p - q
    if edge <= 0.0:
        return 0.0
    return max(0.0, float(k) * edge)


def single_trade_cap_pct(*, amplifier_on: bool, cold_start: bool) -> float:
    """Resolve the single-trade % cap given amplifier + cold-start state."""
    if cold_start:
        return CS3_SINGLE_AMPLIFIED_PCT if amplifier_on else CS3_SINGLE_DEFAULT_PCT
    return SINGLE_TRADE_AMPLIFIED_PCT if amplifier_on else SINGLE_TRADE_DEFAULT_PCT


def kelly_or_cold_start(
    *,
    n_closed: int,
    p: float,
    q: float,
    amplifier_on: bool,
    k: float = KELLY_FRACTION_K,
    threshold: int = CS3_N_THRESHOLD,
) -> KellyDecision:
    """Composed entry-point: produce Kelly fraction + applicable single cap.

    - ``n_closed < threshold`` → ``cold_start=True``, ``kelly_fraction=0``,
      single cap = CS-3 (6% / 7%).
    - Otherwise → Kelly on, single cap = full (8% / 9%).
    """
    cold = is_cold_start(n_closed, threshold=threshold)
    if cold:
        return KellyDecision(
            cold_start=True,
            kelly_fraction=0.0,
            single_cap_pct=single_trade_cap_pct(amplifier_on=amplifier_on, cold_start=True),
        )
    return KellyDecision(
        cold_start=False,
        kelly_fraction=kelly_fraction(p, q, k=k),
        single_cap_pct=single_trade_cap_pct(amplifier_on=amplifier_on, cold_start=False),
    )
