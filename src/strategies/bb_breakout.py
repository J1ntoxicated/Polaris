"""Bollinger Band Breakout strategy — pure (P6).

HYPOTHESIS-002: BTC 1h/4h BB upper breakout (momentum) — RSI mean reversion 반대 방향.

Logic:
- price > upper band (close above) → ENTER_LONG (breakout momentum)
- price < middle (mean reversion 시작) → EXIT
- 그 외 HOLD

Polaris SPOT-only (ADR-001) — long only.
"""
from __future__ import annotations

import math
from statistics import mean, stdev

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_PERIOD = 20
DEFAULT_STD_DEV = 2.0
DEFAULT_TARGET_SIZE_USD = 1000.0


def compute_bb(closes: list[float], period: int, std_dev: float) -> tuple[float, float, float]:
    """Bollinger Band: (lower, middle, upper). Pure P6.

    Returns (0,0,0) if insufficient data.
    """
    if len(closes) < period:
        return (0.0, 0.0, 0.0)
    window = closes[-period:]
    middle = mean(window)
    sd = stdev(window) if period >= 2 else 0.0
    upper = middle + std_dev * sd
    lower = middle - std_dev * sd
    return (lower, middle, upper)


class BBBreakout(Strategy):
    """Bollinger Band breakout (momentum) strategy."""

    name = "bb_breakout"

    def __init__(
        self,
        period: int = DEFAULT_PERIOD,
        std_dev: float = DEFAULT_STD_DEV,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        self.period = period
        self.std_dev = std_dev
        self.target_size_usd = target_size_usd
        self.min_window = period

    def evaluate(self, window: list[Candle]) -> Signal:
        self._ensure_window(window)
        closes = [c.close for c in window]
        lower, middle, upper = compute_bb(closes, self.period, self.std_dev)
        current = closes[-1]
        ts = window[-1].timestamp_ms

        if current > upper:
            # Breakout momentum
            confidence = min(1.0, (current - upper) / max(upper - middle, 1e-9))
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=min(1.0, confidence + 0.5),
                target_size_usd=self.target_size_usd,
                reason=f"close={current:.2f} > upper={upper:.2f}",
            )
        if current < middle:
            # Mean reversion start → exit
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=min(1.0, (middle - current) / max(middle - lower, 1e-9) + 0.3),
                reason=f"close={current:.2f} < middle={middle:.2f}",
            )
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"close={current:.2f} in band [{middle:.2f}, {upper:.2f}]",
        )
