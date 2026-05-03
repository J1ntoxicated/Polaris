"""Backtest engine — pure (P6).

Simple deterministic engine: candle iteration → strategy → trade simulation.
No I/O. All inputs explicit.
"""
from __future__ import annotations

from src.backtest.result import BacktestResult
from src.domain.candle import Candle
from src.domain.metrics import DEFAULT_FEE_ROUND_TRIP, TradeReturn
from src.domain.signal import SignalAction
from src.domain.strategy import Strategy


def backtest(
    candles: list[Candle],
    strategy: Strategy,
    ticker: str,
    timeframe: str,
    fee_round_trip: float = DEFAULT_FEE_ROUND_TRIP,
) -> BacktestResult:
    """Pure backtest. Iterate candles, evaluate strategy, simulate trades.

    Simple model:
    - One open position at a time.
    - Entry on ENTER_LONG/ENTER_SHORT (close price as fill).
    - Exit on EXIT signal OR opposite entry signal (reversal).
    - HOLD = no action.
    - Last candle: force-close open position at last close.

    Args:
        candles: 시간순 candle list.
        strategy: Strategy 인스턴스.
        ticker: e.g. "BTC-USDT".
        timeframe: e.g. "1h".
        fee_round_trip: round-trip fee (default 0.014 — INSIGHT-007 OKX paper Lv1).

    Returns:
        BacktestResult with trades + metrics.
    """
    if len(candles) < strategy.min_window:
        return BacktestResult(
            strategy_name=strategy.name,
            ticker=ticker,
            timeframe=timeframe,
            n_candles=len(candles),
            fee_round_trip=fee_round_trip,
        )

    trades: list[TradeReturn] = []
    open_entry_price: float | None = None
    open_direction: int = 0  # +1 long, -1 short, 0 none

    # window 길이 = i+1. window >= min_window이려면 i >= min_window - 1.
    for i in range(strategy.min_window - 1, len(candles)):
        window = candles[: i + 1]  # inclusive of current
        signal = strategy.evaluate(window)
        current_close = candles[i].close

        # Open position management
        if open_entry_price is not None:
            should_exit = (
                signal.action == SignalAction.EXIT
                or (signal.action == SignalAction.ENTER_LONG and open_direction == -1)
                or (signal.action == SignalAction.ENTER_SHORT and open_direction == 1)
            )
            if should_exit:
                trades.append(
                    TradeReturn(
                        entry_price=open_entry_price,
                        exit_price=current_close,
                        direction=open_direction,
                    )
                )
                open_entry_price = None
                open_direction = 0

        # New entry (after exit OR no open position)
        if open_entry_price is None and signal.action in (
            SignalAction.ENTER_LONG,
            SignalAction.ENTER_SHORT,
        ):
            open_entry_price = current_close
            open_direction = 1 if signal.action == SignalAction.ENTER_LONG else -1

    # Force-close at last candle
    if open_entry_price is not None:
        trades.append(
            TradeReturn(
                entry_price=open_entry_price,
                exit_price=candles[-1].close,
                direction=open_direction,
            )
        )

    return BacktestResult(
        strategy_name=strategy.name,
        ticker=ticker,
        timeframe=timeframe,
        n_candles=len(candles),
        trades=tuple(trades),
        fee_round_trip=fee_round_trip,
    )
