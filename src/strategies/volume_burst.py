"""Volume Burst momentum strategy — pure (P6).

HYPOTHESIS-008: 1h volume > 2x SMA(20) AND close > open (양봉) → ENTER_LONG.
Lower fee 가정 (live LV3 round-trip 0.14%).

Logic:
- volume > 2x SMA(20) volume AND close > open → ENTER_LONG (volume burst momentum)
- close < open OR price < entry × 0.997 (-0.3% SL implicit) → EXIT
- TP target: +0.8% (handled by next signal cycle or runner)
"""
from __future__ import annotations

from statistics import mean

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_VOL_PERIOD = 20
DEFAULT_VOL_MULT = 2.0
DEFAULT_TARGET_SIZE_USD = 250.0  # 5000 × 5%


class VolumeBurst(Strategy):
    name = "volume_burst"

    def __init__(
        self,
        vol_period: int = DEFAULT_VOL_PERIOD,
        vol_mult: float = DEFAULT_VOL_MULT,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        self.vol_period = vol_period
        self.vol_mult = vol_mult
        self.target_size_usd = target_size_usd
        self.min_window = vol_period + 1

    def evaluate(self, window: list[Candle]) -> Signal:
        self._ensure_window(window)
        ts = window[-1].timestamp_ms
        current = window[-1]
        vol_history = [c.volume for c in window[-self.vol_period - 1:-1]]
        avg_vol = mean(vol_history) if vol_history else 0.0

        # Volume burst + bullish candle
        if avg_vol > 0 and current.volume > self.vol_mult * avg_vol and current.close > current.open:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.7,
                target_size_usd=self.target_size_usd,
                reason=f"vol burst {current.volume:.0f} > {self.vol_mult}×{avg_vol:.0f} bullish",
            )
        # Bearish reversal → exit
        if current.close < current.open and avg_vol > 0 and current.volume > avg_vol:
            return Signal(
                timestamp_ms=ts, action=SignalAction.EXIT, confidence=0.6,
                reason=f"bearish reversal vol={current.volume:.0f}",
            )
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"vol {current.volume:.0f} avg {avg_vol:.0f}",
        )
