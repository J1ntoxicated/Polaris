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
    """Pure backtest — look-ahead bias 제거.

    실행 모델 (codex review 2026-05-04 fix):
    - bar `i` close에서 strategy.evaluate(window[:i+1]) 호출
    - 신호 발생 시 **다음 bar `i+1` open**에서 체결 (look-ahead bias 제거)
    - 마지막 bar 신호는 다음 bar 없음 → 체결 X (실제 운영도 다음 cycle까지 wait)
    - 미체결 open position은 마지막 close에서 force-close (out-of-sample 보수적)

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

    # window 길이 = i+1. 신호는 i까지 정보로 결정, 체결은 i+1 open. 마지막 bar 신호는 체결 안 함.
    for i in range(strategy.min_window - 1, len(candles) - 1):
        window = candles[: i + 1]  # inclusive of current (i까지 정보)
        signal = strategy.evaluate(window)
        next_open = candles[i + 1].open  # 다음 bar open (look-ahead 제거)

        # Open position management — exit at next bar open
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
                        exit_price=next_open,
                        direction=open_direction,
                    )
                )
                open_entry_price = None
                open_direction = 0

        # New entry — next bar open
        if open_entry_price is None and signal.action in (
            SignalAction.ENTER_LONG,
            SignalAction.ENTER_SHORT,
        ):
            open_entry_price = next_open
            open_direction = 1 if signal.action == SignalAction.ENTER_LONG else -1

    # Force-close at last candle close (open position 있으면)
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
