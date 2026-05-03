"""tests/strategies/test_trade_flow.py — TradeFlow hysteresis (Codex Round 4 fix)."""
from __future__ import annotations

from src.domain.signal import SignalAction
from src.strategies.trade_flow import (
    DEFAULT_BUY_RATIO,
    DEFAULT_SELL_RATIO,
    TradeFlow,
)


def _tick(ts: int = 1) -> dict:
    return {"ts": ts, "last": 100.0, "bid": 99.9, "ask": 100.1}


class TestHysteresis:
    def test_entry_threshold_065(self) -> None:
        assert DEFAULT_BUY_RATIO == 0.65

    def test_exit_threshold_045(self) -> None:
        assert DEFAULT_SELL_RATIO == 0.45

    def test_deadzone_no_signal(self) -> None:
        s = TradeFlow()
        for ratio in (0.46, 0.50, 0.55, 0.60, 0.64):
            sig = s.evaluate_flow(_tick(), ratio, n_trades=100)
            assert sig.action == SignalAction.HOLD, f"ratio {ratio} should HOLD"

    def test_strong_buy_enters(self) -> None:
        s = TradeFlow()
        sig = s.evaluate_flow(_tick(), 0.65, n_trades=100)
        assert sig.action == SignalAction.ENTER_LONG

    def test_strong_sell_exits(self) -> None:
        s = TradeFlow()
        sig = s.evaluate_flow(_tick(), 0.45, n_trades=100)
        assert sig.action == SignalAction.EXIT


class TestMinTrades:
    def test_insufficient_trades_holds(self) -> None:
        s = TradeFlow()
        sig = s.evaluate_flow(_tick(), 0.9, n_trades=10)
        assert sig.action == SignalAction.HOLD
