"""RSI Mean Reversion strategy — pure (P6).

HYPOTHESIS-001: BTC-USDT 1h, RSI(14) mean reversion이 fee 1.4% round-trip 후 expectancy > 0인가?

Logic:
- RSI < oversold_threshold → ENTER_LONG
- RSI > overbought_threshold → EXIT (long position 종료)
- 그 외 HOLD

Pure: no I/O, deterministic, side-effect free.
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_PERIOD = 14
DEFAULT_OVERSOLD = 30.0
DEFAULT_OVERBOUGHT = 70.0
DEFAULT_TARGET_SIZE_USD = 1000.0  # ADR-007 paper sizing


def compute_rsi(closes: list[float], period: int = DEFAULT_PERIOD) -> float:
    """Wilder's RSI (단순화 — exponential smoothing).

    Args:
        closes: 시간순 close 가격 list (period+1 이상).
        period: RSI period (default 14).

    Returns:
        RSI value in [0, 100]. 변화 없음 → 50.

    Pure function (P6).
    """
    if len(closes) < period + 1:
        return 50.0  # 데이터 부족 → neutral
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    # Wilder smoothing for remaining
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0)
        loss = max(-diff, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class RSIMeanReversion(Strategy):
    """RSI mean reversion strategy."""

    name = "rsi_mean_reversion"

    def __init__(
        self,
        period: int = DEFAULT_PERIOD,
        oversold: float = DEFAULT_OVERSOLD,
        overbought: float = DEFAULT_OVERBOUGHT,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.target_size_usd = target_size_usd
        self.min_window = period + 1

    def evaluate(self, window: list[Candle]) -> Signal:
        self._ensure_window(window)
        closes = [c.close for c in window]
        rsi = compute_rsi(closes, self.period)
        ts = window[-1].timestamp_ms

        if rsi <= self.oversold:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=min(1.0, (self.oversold - rsi) / self.oversold + 0.5),
                target_size_usd=self.target_size_usd,
                reason=f"rsi={rsi:.1f} <= {self.oversold}",
            )
        if rsi >= self.overbought:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=min(1.0, (rsi - self.overbought) / (100 - self.overbought) + 0.5),
                reason=f"rsi={rsi:.1f} >= {self.overbought}",
            )
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"rsi={rsi:.1f} in band",
        )
