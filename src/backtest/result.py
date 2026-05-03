"""Backtest result — pure (P6)."""
from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.metrics import (
    DEFAULT_FEE_ROUND_TRIP,
    TradeReturn,
    expectancy,
    hit_rate,
    max_drawdown,
    sharpe,
)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Backtest output — immutable.

    All metrics computed from `trades` + `fee_round_trip`.
    """

    strategy_name: str
    ticker: str
    timeframe: str
    n_candles: int
    trades: tuple[TradeReturn, ...] = field(default_factory=tuple)
    fee_round_trip: float = DEFAULT_FEE_ROUND_TRIP

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def hit_rate(self) -> float:
        return hit_rate(list(self.trades), self.fee_round_trip)

    @property
    def expectancy(self) -> float:
        return expectancy(list(self.trades), self.fee_round_trip)

    @property
    def sharpe(self) -> float:
        return sharpe(list(self.trades), self.fee_round_trip)

    @property
    def max_drawdown(self) -> float:
        return max_drawdown(list(self.trades), self.fee_round_trip)

    def summary(self) -> dict:
        return {
            "strategy": self.strategy_name,
            "ticker": self.ticker,
            "timeframe": self.timeframe,
            "n_candles": self.n_candles,
            "n_trades": self.n_trades,
            "hit_rate": round(self.hit_rate, 4),
            "expectancy": round(self.expectancy, 6),
            "sharpe": round(self.sharpe, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "fee_round_trip": self.fee_round_trip,
        }
