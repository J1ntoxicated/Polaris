"""Tick Momentum strategy — pure (P6).

HYPOTHESIS-010: WebSocket tick payload 활용 — candle indicator 무관.

Logic (tick 매번 평가):
- 24h change > +3% AND last > 24h high × 0.995 (high 근처) → ENTER_LONG (breakout momentum)
- 24h change < -3% AND last < 24h low × 1.005 → EXIT (panic exit)
- bid/ask spread 측정 (entry 시 spread 좁아야 — 유동성 좋음)
- vol24h > avg → 추가 가산점

Tick-driven evaluator (multi-tf candle 무관).
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_CHANGE_THRESHOLD = 0.015  # 1.5% (3% → 1.5% 완화 — 더 자주 trigger)
DEFAULT_HIGH_PROXIMITY = 0.99   # 0.995 → 0.99 (1% 이내 high 근처)
DEFAULT_LOW_PROXIMITY = 1.01
DEFAULT_MAX_SPREAD_BPS = 50     # 20 → 50 bps (더 많은 ticker 통과)
DEFAULT_TARGET_SIZE_USD = 200.0


class TickMomentum(Strategy):
    """Tick-driven momentum — uses WebSocket tick payload, not candle.

    NOTE: standard `evaluate(window)` 호출 시 HOLD 반환 (tick 데이터 필요).
    `evaluate_tick(tick)` 사용.
    """

    name = "tick_momentum"
    min_window = 1

    def __init__(
        self,
        change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
        high_proximity: float = DEFAULT_HIGH_PROXIMITY,
        low_proximity: float = DEFAULT_LOW_PROXIMITY,
        max_spread_bps: float = DEFAULT_MAX_SPREAD_BPS,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        self.change_threshold = change_threshold
        self.high_proximity = high_proximity
        self.low_proximity = low_proximity
        self.max_spread_bps = max_spread_bps
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        ts = window[-1].timestamp_ms if window else 0
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason="TickMomentum requires tick — use evaluate_tick",
        )

    def evaluate_tick(self, tick: dict) -> Signal:
        """tick payload 직접 평가."""
        last = float(tick.get("last", 0) or 0)
        bid = float(tick.get("bid", 0) or 0)
        ask = float(tick.get("ask", 0) or 0)
        open24 = float(tick.get("open24h", 0) or 0)
        high24 = float(tick.get("high24h", 0) or 0)
        low24 = float(tick.get("low24h", 0) or 0)
        ts = int(tick.get("ts", 0) or 0)

        if last <= 0 or open24 <= 0:
            return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                          reason="invalid tick")

        change_24h = (last - open24) / open24
        spread_bps = (ask - bid) / last * 10000 if (bid > 0 and ask > 0) else 0
        high_dist = (high24 - last) / last if last > 0 else 1.0

        # Spread filter — 유동성 떨어지면 skip
        if spread_bps > self.max_spread_bps:
            return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                          reason=f"spread {spread_bps:.1f}bps > {self.max_spread_bps}")

        # ENTER_LONG: 강한 24h 상승 + 24h high 근처 (breakout momentum)
        if change_24h >= self.change_threshold and last > high24 * self.high_proximity:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.8,
                target_size_usd=self.target_size_usd,
                reason=(
                    f"momentum +{change_24h*100:.1f}% high24={high24:.4g} "
                    f"last={last:.4g} spread={spread_bps:.1f}bps"
                ),
            )

        # EXIT: 24h 큰 하락 + low 근처 (panic exit)
        if change_24h <= -self.change_threshold and last < low24 * self.low_proximity:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.7,
                reason=f"panic exit {change_24h*100:.1f}% low24={low24:.4g}",
            )

        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"24h={change_24h*100:+.2f}% spread={spread_bps:.1f}bps high_dist={high_dist*100:.2f}%",
        )
