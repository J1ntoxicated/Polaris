"""MACD Trend strategy — pure (P6).

HYPOTHESIS-005: MACD signal-line crossover for 1d trend following.
- MACD > signal line + bullish histogram → ENTER_LONG
- MACD < signal line → EXIT
- Long-only (Polaris ADR-001 SPOT).
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_FAST = 12
DEFAULT_SLOW = 26
DEFAULT_SIGNAL = 9
DEFAULT_TARGET_SIZE_USD = 1000.0


def _ema(values: list[float], period: int) -> float:
    """Exponential MA. Returns 0 if insufficient data."""
    if len(values) < period:
        return 0.0
    multiplier = 2.0 / (period + 1)
    # Seed: SMA of first `period` values
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = (v - ema) * multiplier + ema
    return ema


def compute_macd(closes: list[float], fast: int, slow: int, signal: int) -> tuple[float, float, float]:
    """Returns (macd, signal_line, histogram). All zero if insufficient data."""
    if len(closes) < slow + signal:
        return (0.0, 0.0, 0.0)
    # MACD line: EMA(fast) - EMA(slow), computed across rolling windows
    macd_series = []
    for i in range(slow, len(closes) + 1):
        sub = closes[:i]
        macd_series.append(_ema(sub, fast) - _ema(sub, slow))
    if len(macd_series) < signal:
        return (0.0, 0.0, 0.0)
    macd = macd_series[-1]
    signal_line = sum(macd_series[-signal:]) / signal  # 단순화: SMA of last `signal` MACDs
    return (macd, signal_line, macd - signal_line)


class MACDTrend(Strategy):
    name = "macd_trend"

    def __init__(
        self,
        fast: int = DEFAULT_FAST,
        slow: int = DEFAULT_SLOW,
        signal: int = DEFAULT_SIGNAL,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        if fast >= slow:
            raise ValueError(f"fast {fast} must be < slow {slow}")
        self.fast = fast
        self.slow = slow
        self.signal_period = signal
        self.target_size_usd = target_size_usd
        self.min_window = slow + signal + 1

    def evaluate(self, window: list[Candle]) -> Signal:
        self._ensure_window(window)
        closes = [c.close for c in window]
        macd_now, signal_now, hist_now = compute_macd(closes, self.fast, self.slow, self.signal_period)
        macd_prev, signal_prev, _ = compute_macd(closes[:-1], self.fast, self.slow, self.signal_period)
        ts = window[-1].timestamp_ms

        # Bullish crossover (MACD crosses above signal)
        if macd_prev <= signal_prev and macd_now > signal_now and hist_now > 0:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.7,
                target_size_usd=self.target_size_usd,
                reason=f"MACD cross up: macd={macd_now:.4f} > signal={signal_now:.4f}",
            )
        # Bearish crossover
        if macd_prev >= signal_prev and macd_now < signal_now:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.7,
                reason=f"MACD cross down: macd={macd_now:.4f} < signal={signal_now:.4f}",
            )
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"MACD={macd_now:.4f} signal={signal_now:.4f}",
        )
