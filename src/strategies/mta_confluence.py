"""Multi-Timeframe Analysis (MTA) Confluence — pure (P6).

HYPOTHESIS-010: high-probability long entry — 4 timeframe confluence.

Logic (모두 만족 시 ENTER_LONG):
- 1d trend UP: SMA(20) > SMA(50) (long-term bullish)
- 4h zone: pullback (price < 4h SMA(20) but > 4h SMA(50)) — 매수 zone
- 1h trigger: RSI(14) < 45 (oversold-ish in higher TF zone)
- 15m timing: bullish candle (close > open) AND close > prev close

Exit:
- 1h RSI > 65 (overbought) → EXIT
- 15m close < prev close × 0.997 (-0.3% SL implicit) — TP/SL은 runner enforces

특이: 이 strategy는 evaluate(window) interface 변형 — multi-tf data 받음.
"""
from __future__ import annotations

from statistics import mean

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy
from src.strategies.rsi_mean_reversion import compute_rsi


def _sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return 0.0
    return mean(values[-period:])


class MTAConfluence(Strategy):
    """Multi-Timeframe Confluence — 4 timeframe 모두 정합 시 entry.

    NOTE: standard `evaluate(window)` 대신 `evaluate_multi_tf(tf_data)` 사용.
    cron / runner에서는 multi-tf wrapper 필요.
    """

    name = "mta_confluence"
    min_window = 50  # primary tf (15m) 기준

    def __init__(self, target_size_usd: float = 250.0):
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        """Single-tf fallback — confluence 평가 불가, HOLD 반환."""
        ts = window[-1].timestamp_ms if window else 0
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason="MTA requires multi-tf data — use evaluate_multi_tf",
        )

    def evaluate_multi_tf(self, tf_data: dict[str, list[Candle]]) -> Signal:
        """4 timeframe 평가 → confluence signal."""
        d1 = tf_data.get("1D", [])
        h4 = tf_data.get("4H", [])
        h1 = tf_data.get("1H", [])
        m15 = tf_data.get("15m", [])

        if not (len(d1) >= 50 and len(h4) >= 50 and len(h1) >= 14 and len(m15) >= 2):
            return Signal(
                timestamp_ms=m15[-1].timestamp_ms if m15 else 0,
                action=SignalAction.HOLD, confidence=0.0,
                reason="insufficient multi-tf data",
            )

        d1_closes = [c.close for c in d1]
        h4_closes = [c.close for c in h4]
        h1_closes = [c.close for c in h1]
        ts = m15[-1].timestamp_ms

        # 1d trend
        d1_sma20 = _sma(d1_closes, 20)
        d1_sma50 = _sma(d1_closes, 50)
        d1_uptrend = d1_sma20 > d1_sma50

        # 4h zone (pullback)
        h4_sma20 = _sma(h4_closes, 20)
        h4_sma50 = _sma(h4_closes, 50)
        h4_close = h4_closes[-1]
        h4_pullback = h4_close < h4_sma20 and h4_close > h4_sma50

        # 1h trigger (RSI oversold-ish in zone)
        h1_rsi = compute_rsi(h1_closes, 14)
        h1_trigger = h1_rsi < 45

        # 15m timing (bullish candle + close > prev close)
        m15_curr = m15[-1]
        m15_prev = m15[-2]
        m15_bullish = m15_curr.close > m15_curr.open and m15_curr.close > m15_prev.close

        # Exit condition: 1h RSI overbought
        if h1_rsi > 65:
            return Signal(
                timestamp_ms=ts, action=SignalAction.EXIT, confidence=0.7,
                reason=f"1h RSI {h1_rsi:.1f} > 65 overbought",
            )

        # Confluence ENTRY
        if d1_uptrend and h4_pullback and h1_trigger and m15_bullish:
            return Signal(
                timestamp_ms=ts, action=SignalAction.ENTER_LONG,
                confidence=0.85,
                target_size_usd=self.target_size_usd,
                reason=f"MTA confluence: 1d↑ 4h pullback 1h_rsi={h1_rsi:.1f} 15m bullish",
            )

        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"1d↑={d1_uptrend} 4h_pull={h4_pullback} 1h_rsi={h1_rsi:.1f} 15m_bull={m15_bullish}",
        )
