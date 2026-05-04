"""Funding Rate Filter — pure (P6).

HYPOTHESIS-027 (HYPO-015 부활): Binance Futures funding rate as entry weight modifier.

Role: NOT a standalone entry strategy. Modifies size weight for other strategies.
  - funding_8h <= -0.05% → shorts being squeezed out → bullish impulse likely
    → size_multiplier = 1.20 (20% size boost)
  - funding_8h >= +0.10% → longs paying, over-leveraged → entry blocked
    → size_multiplier = 0.0 (block)
  - Otherwise: neutral → size_multiplier = 1.0

Source: Binance Futures REST /fapi/v1/premiumIndex (poll every 60s by shell).
Shell caller passes funding_8h as float; this module is pure.

Usage pattern (shell side):
    modifier = FundingRateFilter().compute_multiplier(funding_8h=rate)
    adjusted_size = base_size * modifier
    if modifier == 0.0:
        skip entry

Pure function — no I/O, no REST calls. Shell caller fetches and passes funding rate.
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_SQUEEZE_THRESHOLD: float = -0.0005   # -0.05%: shorts squeezed → bullish
DEFAULT_BLOCK_THRESHOLD: float = 0.0010      # +0.10%: longs over-leveraged → block
DEFAULT_BOOST_MULTIPLIER: float = 1.20       # 20% size boost on squeeze signal
BLOCK_MULTIPLIER: float = 0.0
NEUTRAL_MULTIPLIER: float = 1.0


class FundingRateFilter(Strategy):
    """Funding rate entry size modifier.

    Primary interface: `compute_multiplier(funding_8h)` — returns float multiplier.
    `evaluate(window)` returns HOLD (not a standalone strategy).
    """

    name = "funding_rate_filter"
    min_window = 1

    def __init__(
        self,
        squeeze_threshold: float = DEFAULT_SQUEEZE_THRESHOLD,
        block_threshold: float = DEFAULT_BLOCK_THRESHOLD,
        boost_multiplier: float = DEFAULT_BOOST_MULTIPLIER,
    ) -> None:
        self.squeeze_threshold = squeeze_threshold
        self.block_threshold = block_threshold
        self.boost_multiplier = boost_multiplier

    def evaluate(self, window: list[Candle]) -> Signal:
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="FundingRateFilter is a size modifier — use compute_multiplier",
        )

    def compute_multiplier(self, funding_8h: float | None) -> float:
        """Compute size multiplier based on funding rate.

        Args:
            funding_8h: Binance 8h funding rate as decimal (e.g. -0.0005 = -0.05%).
                        None → neutral (1.0).

        Returns:
            float multiplier: 0.0 (block) | 1.0 (neutral) | boost_multiplier (boost).

        Pure function — no I/O, deterministic.
        """
        if funding_8h is None:
            return NEUTRAL_MULTIPLIER

        # Longs over-leveraged → block entry
        if funding_8h >= self.block_threshold:
            return BLOCK_MULTIPLIER

        # Shorts squeezed → bullish impulse → boost
        if funding_8h <= self.squeeze_threshold:
            return self.boost_multiplier

        return NEUTRAL_MULTIPLIER

    def describe(self, funding_8h: float | None) -> str:
        """Human-readable description of the funding rate signal.

        Pure function — no I/O.
        """
        if funding_8h is None:
            return "funding_rate: unknown (neutral)"
        m = self.compute_multiplier(funding_8h)
        if m == BLOCK_MULTIPLIER:
            return f"funding_rate: BLOCK ({funding_8h*100:+.4f}% >= {self.block_threshold*100:+.2f}%)"
        if m > NEUTRAL_MULTIPLIER:
            return f"funding_rate: BOOST x{m:.2f} ({funding_8h*100:+.4f}% <= {self.squeeze_threshold*100:+.2f}%)"
        return f"funding_rate: neutral ({funding_8h*100:+.4f}%)"
