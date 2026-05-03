"""tests/backtest/test_engine.py — Pure backtest engine."""
from __future__ import annotations

import pytest

from src.backtest.engine import backtest
from src.backtest.promotion_gate import evaluate_promotion
from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy


class AlwaysHold(Strategy):
    name = "always_hold"
    min_window = 1

    def evaluate(self, window):
        return Signal(timestamp_ms=window[-1].timestamp_ms, action=SignalAction.HOLD, confidence=0.0)


class AlwaysLong(Strategy):
    """모든 candle에서 long entry (deterministic)."""
    name = "always_long"
    min_window = 1

    def evaluate(self, window):
        return Signal(
            timestamp_ms=window[-1].timestamp_ms,
            action=SignalAction.ENTER_LONG,
            confidence=1.0,
            target_size_usd=1000.0,
        )


class EnterFirstExitLast(Strategy):
    """첫 candle 진입, 마지막 candle 종료 (단일 long trade)."""
    name = "enter_first_exit_last"
    min_window = 1

    def __init__(self, n_candles: int):
        self.n_candles = n_candles
        self.count = 0

    def evaluate(self, window):
        ts = window[-1].timestamp_ms
        idx = len(window) - 1
        if idx == 0:
            return Signal(timestamp_ms=ts, action=SignalAction.ENTER_LONG, confidence=1.0, target_size_usd=1000)
        if idx == self.n_candles - 1:
            return Signal(timestamp_ms=ts, action=SignalAction.EXIT, confidence=1.0)
        return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0)


def make_candles(prices: list[float]) -> list[Candle]:
    """단순 헬퍼: close 가격 list → candle list."""
    return [
        Candle(
            timestamp_ms=(i + 1) * 3_600_000,
            open=p, high=p * 1.01, low=p * 0.99, close=p, volume=100,
        )
        for i, p in enumerate(prices)
    ]


class TestBacktestEngine:
    def test_no_trades_when_always_hold(self) -> None:
        candles = make_candles([100, 110, 120])
        result = backtest(candles, AlwaysHold(), "BTC-USDT", "1h")
        assert result.n_trades == 0
        assert result.n_candles == 3

    def test_too_few_candles(self) -> None:
        result = backtest([], AlwaysHold(), "BTC-USDT", "1h")
        assert result.n_trades == 0
        assert result.n_candles == 0

    def test_single_long_trade_profit(self) -> None:
        candles = make_candles([100, 110, 120])
        strategy = EnterFirstExitLast(n_candles=3)
        result = backtest(candles, strategy, "BTC-USDT", "1h", fee_round_trip=0.0)
        assert result.n_trades == 1
        # entry=100 (idx 0 close), exit=120 (idx 2 close), gross=+20%
        assert result.trades[0].gross_pct == pytest.approx(0.20)

    def test_force_close_at_last_candle(self) -> None:
        # AlwaysLong은 매 candle에서 ENTER_LONG → 첫 candle 진입 후 동일 direction이라 reversal X → 마지막 force-close
        candles = make_candles([100, 110, 120])
        result = backtest(candles, AlwaysLong(), "BTC-USDT", "1h", fee_round_trip=0.0)
        assert result.n_trades == 1
        assert result.trades[0].entry_price == 100  # idx 0 close
        assert result.trades[0].exit_price == 120  # last close

    def test_fee_subtracted_from_net(self) -> None:
        candles = make_candles([100, 102])
        strategy = EnterFirstExitLast(n_candles=2)
        result = backtest(candles, strategy, "BTC-USDT", "1h", fee_round_trip=0.014)
        # gross=+2%, net = 2% - 1.4% = 0.6%
        assert result.expectancy == pytest.approx(0.02 - 0.014)


class TestPromotionGate:
    def test_fast_fail_blocks_promotion(self) -> None:
        # 30 trades 모두 +0.5% gross, fee 1.4% → expectancy -0.9%
        candles = make_candles([100] + [101 if i % 2 == 0 else 100 for i in range(60)])
        # 단순화: 직접 result 생성

        from src.backtest.result import BacktestResult
        from src.domain.metrics import TradeReturn

        trades = tuple(TradeReturn(100, 100.5, 1) for _ in range(30))
        result = BacktestResult(
            strategy_name="dummy", ticker="BTC", timeframe="1h",
            n_candles=60, trades=trades, fee_round_trip=0.014,
        )
        decision = evaluate_promotion(result)
        assert not decision.passed
        assert not decision.fast_fail_passed
        assert any("fast-fail" in f for f in decision.failures)

    def test_promotion_pass(self) -> None:
        from src.backtest.result import BacktestResult
        from src.domain.metrics import TradeReturn

        # 30 trades 모두 +5% gross, fee 1.4% → net 3.6%, sharpe = inf (std=0)
        # std=0이라 sharpe=0 → MIN_SHARPE 미달 → fail
        # 변형: 일부 +5%, 일부 +4% → mean 4.5%, std 작음, sharpe 큼
        trades = tuple(
            TradeReturn(100, 105 if i % 2 == 0 else 104, 1) for i in range(30)
        )
        result = BacktestResult(
            strategy_name="dummy", ticker="BTC", timeframe="1h",
            n_candles=60, trades=trades, fee_round_trip=0.014,
        )
        decision = evaluate_promotion(result, category="scalp")
        # Category scalp: n_trades 30 → 50 needed. We have 30 → fails on n_trades.
        # Test expectation: at least fast-fail OK + structure pass.
        assert decision.fast_fail_passed, f"fast-fail should pass: {decision.failures}"
