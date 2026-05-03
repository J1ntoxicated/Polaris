"""Breakout Momentum strategy — pure (P6).

HYPOTHESIS-009: 5-bar high breakout + bullish candle = trend continuation entry.
현재 시장 trend-up 정합 (mean reversion 보다 active).

Logic:
- close > max(high of last 5 bars, exclusive) AND close > open → ENTER_LONG
- close < min(low of last 5 bars, exclusive) → EXIT
- Long-only.
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_LOOKBACK = 5
DEFAULT_TARGET_SIZE_USD = 200.0


class BreakoutMomentum(Strategy):
    name = "breakout_momentum"

    def __init__(
        self,
        lookback: int = DEFAULT_LOOKBACK,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        self.lookback = lookback
        self.target_size_usd = target_size_usd
        self.min_window = lookback + 1

    def evaluate(self, window: list[Candle]) -> Signal:
        self._ensure_window(window)
        ts = window[-1].timestamp_ms
        current = window[-1]
        prev_window = window[-self.lookback - 1:-1]
        prev_high = max(c.high for c in prev_window)
        prev_low = min(c.low for c in prev_window)

        if current.close > prev_high and current.close > current.open:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.7,
                target_size_usd=self.target_size_usd,
                reason=f"close {current.close:.4g} > {self.lookback}-bar high {prev_high:.4g}",
            )
        if current.close < prev_low:
            return Signal(
                timestamp_ms=ts, action=SignalAction.EXIT, confidence=0.6,
                reason=f"close {current.close:.4g} < {self.lookback}-bar low {prev_low:.4g}",
            )
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"close {current.close:.4g} in range",
        )
