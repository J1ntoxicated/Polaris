"""Order Book Imbalance strategy — pure (P6).

HYPOTHESIS-011: OKX books5 bid/ask depth imbalance.

Logic (tick + book):
- imbalance = bid_sum / (bid_sum + ask_sum) on top 5 levels
- imbalance > 0.65 + 24h change > 0 → ENTER_LONG (bullish pressure)
- imbalance < 0.35 → EXIT (bearish flip)
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_IMBALANCE_BUY = 0.62
DEFAULT_IMBALANCE_SELL = 0.38
DEFAULT_TARGET_SIZE_USD = 200.0


class OrderBookImbalance(Strategy):
    name = "orderbook_imbalance"
    min_window = 1

    def __init__(
        self,
        imbalance_buy: float = DEFAULT_IMBALANCE_BUY,
        imbalance_sell: float = DEFAULT_IMBALANCE_SELL,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        self.imbalance_buy = imbalance_buy
        self.imbalance_sell = imbalance_sell
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        ts = window[-1].timestamp_ms if window else 0
        return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                      reason="needs imbalance — use evaluate_book")

    def evaluate_book(self, tick: dict, imbalance: float) -> Signal:
        """tick + book imbalance ratio."""
        ts = int(tick.get("ts", 0) or 0)
        last = float(tick.get("last", 0) or 0)
        open24 = float(tick.get("open24h", 0) or 0)
        change_24h = (last - open24) / open24 if open24 > 0 else 0

        if imbalance >= self.imbalance_buy and change_24h > 0:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.8,
                target_size_usd=self.target_size_usd,
                reason=f"book imbalance {imbalance:.2f} >= {self.imbalance_buy} + 24h+{change_24h*100:.2f}%",
            )
        if imbalance <= self.imbalance_sell:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.7,
                reason=f"book imbalance {imbalance:.2f} <= {self.imbalance_sell} bearish",
            )
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"imbalance {imbalance:.2f}",
        )
