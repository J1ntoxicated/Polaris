"""Donchian Channel Breakout — pure (P6).

HYPOTHESIS-004: BTC 1d Donchian(20/55) channel breakout (Turtle Trading 변형).
- close > N-day high → ENTER_LONG
- close < N-day exit_low → EXIT
- Long-only (Polaris ADR-001 SPOT).
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_ENTRY_PERIOD = 20
DEFAULT_EXIT_PERIOD = 10
DEFAULT_TARGET_SIZE_USD = 1000.0


def compute_channel(candles: list[Candle], period: int) -> tuple[float, float]:
    """Returns (lowest_low, highest_high) over last `period` candles."""
    if len(candles) < period:
        return (0.0, 0.0)
    window = candles[-period:]
    return (min(c.low for c in window), max(c.high for c in window))


class DonchianBreakout(Strategy):
    name = "donchian_breakout"

    def __init__(
        self,
        entry_period: int = DEFAULT_ENTRY_PERIOD,
        exit_period: int = DEFAULT_EXIT_PERIOD,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        if exit_period >= entry_period:
            raise ValueError(f"exit_period {exit_period} must be < entry_period {entry_period}")
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.target_size_usd = target_size_usd
        self.min_window = max(entry_period, exit_period) + 1

    def evaluate(self, window: list[Candle]) -> Signal:
        self._ensure_window(window)
        ts = window[-1].timestamp_ms
        current_close = window[-1].close

        # Entry channel — exclude current bar (look-ahead 방지)
        prev_window = window[:-1]
        _, entry_high = compute_channel(prev_window, self.entry_period)
        exit_low, _ = compute_channel(prev_window, self.exit_period)

        if current_close > entry_high and entry_high > 0:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.7,
                target_size_usd=self.target_size_usd,
                reason=f"close={current_close:.2f} > entry_high={entry_high:.2f}",
            )
        if current_close < exit_low and exit_low > 0:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.7,
                reason=f"close={current_close:.2f} < exit_low={exit_low:.2f}",
            )
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"close={current_close:.2f} in channel [{exit_low:.2f}, {entry_high:.2f}]",
        )
