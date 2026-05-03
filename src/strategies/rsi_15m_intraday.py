"""RSI 15m Intraday Mean Reversion — pure (P6).

HYPOTHESIS-007: 15m RSI(14) < 28 oversold → 반등 확인 후 ENTER_LONG.
Live fee 가정 (OKX LV3 taker 0.07% × 2 = 0.14% round-trip).

Logic:
- RSI(14) < 28 (oversold) AND prev RSI < current RSI (반등 확인) → ENTER_LONG
- TP +0.6% or RSI > 60 → EXIT
- SL implicit via signal (다음 cycle EXIT 신호)
- Long-only (Polaris ADR-001 SPOT)

References:
- INSIGHT-007 fee 가정 변경 (live LV3+)
- ADR-010 daily loss 5% (runner enforces)
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy
from src.strategies.rsi_mean_reversion import compute_rsi

DEFAULT_PERIOD = 14
DEFAULT_OVERSOLD = 28.0
DEFAULT_TP_PCT = 0.006  # 0.6%
DEFAULT_SL_PCT = 0.0035  # 0.35%
DEFAULT_RSI_EXIT = 60.0
DEFAULT_TARGET_SIZE_USD = 200.0  # 보수적 (5000 × 4%)


class RSI15mIntraday(Strategy):
    """RSI 15m oversold 반등 entry. paper Lv3+ fee 가정."""

    name = "rsi_15m_intraday"

    def __init__(
        self,
        period: int = DEFAULT_PERIOD,
        oversold: float = DEFAULT_OVERSOLD,
        rsi_exit: float = DEFAULT_RSI_EXIT,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        self.period = period
        self.oversold = oversold
        self.rsi_exit = rsi_exit
        self.target_size_usd = target_size_usd
        self.min_window = period + 2  # prev + current RSI 계산

    def evaluate(self, window: list[Candle]) -> Signal:
        self._ensure_window(window)
        closes = [c.close for c in window]
        rsi_now = compute_rsi(closes, self.period)
        rsi_prev = compute_rsi(closes[:-1], self.period)
        ts = window[-1].timestamp_ms

        # Oversold reversal: prev < oversold and current rising back above
        if rsi_prev < self.oversold and rsi_now > self.oversold:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.7,
                target_size_usd=self.target_size_usd,
                reason=f"RSI bounce: prev={rsi_prev:.1f}<{self.oversold} now={rsi_now:.1f}>",
            )
        # Exit: RSI > exit threshold (overbought)
        if rsi_now >= self.rsi_exit:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.7,
                reason=f"RSI exit: {rsi_now:.1f} >= {self.rsi_exit}",
            )
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"RSI={rsi_now:.1f}",
        )
