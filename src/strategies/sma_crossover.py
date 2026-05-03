"""SMA Crossover trend-following — pure (P6).

HYPOTHESIS-003: Golden cross (fast SMA > slow SMA) ENTER_LONG, death cross EXIT.
SPOT-only (long-only). Trend following 카테고리 (RSI mean reversion / BB momentum 외).
"""
from __future__ import annotations

from statistics import mean

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_FAST = 10
DEFAULT_SLOW = 30
DEFAULT_TARGET_SIZE_USD = 1000.0


def compute_sma(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return 0.0
    return mean(closes[-period:])


class SMACrossover(Strategy):
    name = "sma_crossover"

    def __init__(
        self,
        fast: int = DEFAULT_FAST,
        slow: int = DEFAULT_SLOW,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        if fast >= slow:
            raise ValueError(f"fast {fast} must be < slow {slow}")
        self.fast = fast
        self.slow = slow
        self.target_size_usd = target_size_usd
        self.min_window = slow + 1  # 이전 + 현재 비교

    def evaluate(self, window: list[Candle]) -> Signal:
        self._ensure_window(window)
        closes = [c.close for c in window]
        fast_now = compute_sma(closes, self.fast)
        slow_now = compute_sma(closes, self.slow)
        fast_prev = compute_sma(closes[:-1], self.fast)
        slow_prev = compute_sma(closes[:-1], self.slow)
        ts = window[-1].timestamp_ms

        # Golden cross
        if fast_prev <= slow_prev and fast_now > slow_now:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.7,
                target_size_usd=self.target_size_usd,
                reason=f"golden cross: fast={fast_now:.2f} > slow={slow_now:.2f}",
            )
        # Death cross
        if fast_prev >= slow_prev and fast_now < slow_now:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.7,
                reason=f"death cross: fast={fast_now:.2f} < slow={slow_now:.2f}",
            )
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"trend continuing fast={fast_now:.2f} slow={slow_now:.2f}",
        )
