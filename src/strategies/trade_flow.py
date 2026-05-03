"""Trade Flow strategy — pure (P6).

HYPOTHESIS-012: OKX trades channel — taker buy/sell volume ratio.

Logic:
- taker_buy_ratio = buy_vol / total_vol (recent 100 trades)
- > 0.6 → ENTER_LONG (aggressive buying)
- < 0.4 → EXIT (aggressive selling)
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

# Codex Round 4 fix: hysteresis 강화 — entry 0.65 / exit 0.45 (deadzone 0.45-0.65)
# 변경 전 0.60/0.40 — 0.45-0.55 영역에서 flip-flop → fee bleed (-$10 in 30min)
DEFAULT_BUY_RATIO = 0.65
DEFAULT_SELL_RATIO = 0.45
DEFAULT_MIN_TRADES = 30
# Phase 2g size cap: post-Round 4 측정 win=17% (post-fee EV 음수 의심) — 알파 증명까지 size↓
# 200 → 100 (50% 감소). 학습 데이터 수집 유지 + 손실 cap.
DEFAULT_TARGET_SIZE_USD = 100.0


class TradeFlow(Strategy):
    name = "trade_flow"
    min_window = 1

    def __init__(
        self,
        buy_ratio: float = DEFAULT_BUY_RATIO,
        sell_ratio: float = DEFAULT_SELL_RATIO,
        min_trades: int = DEFAULT_MIN_TRADES,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        self.buy_ratio = buy_ratio
        self.sell_ratio = sell_ratio
        self.min_trades = min_trades
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        ts = window[-1].timestamp_ms if window else 0
        return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                      reason="needs trade flow — use evaluate_flow")

    def evaluate_flow(self, tick: dict, taker_buy_ratio: float, n_trades: int) -> Signal:
        ts = int(tick.get("ts", 0) or 0)
        if n_trades < self.min_trades:
            return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                          reason=f"insufficient trades {n_trades} < {self.min_trades}")
        if taker_buy_ratio >= self.buy_ratio:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.75,
                target_size_usd=self.target_size_usd,
                reason=f"taker buy {taker_buy_ratio:.2f} >= {self.buy_ratio} (n={n_trades})",
            )
        if taker_buy_ratio <= self.sell_ratio:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.7,
                reason=f"taker buy {taker_buy_ratio:.2f} <= {self.sell_ratio} bearish",
            )
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"taker buy {taker_buy_ratio:.2f}",
        )
