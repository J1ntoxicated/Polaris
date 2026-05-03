"""tests/strategies/test_orderbook_imbalance.py — hysteresis (Codex Round 4 fix)."""
from __future__ import annotations

from src.domain.signal import SignalAction
from src.strategies.orderbook_imbalance import (
    DEFAULT_IMBALANCE_BUY,
    DEFAULT_IMBALANCE_SELL,
    OrderBookImbalance,
)


def _tick(open24: float = 99.0, last: float = 100.0) -> dict:
    return {"ts": 1, "last": last, "open24h": open24}


class TestHysteresis:
    def test_entry_threshold_068(self) -> None:
        assert DEFAULT_IMBALANCE_BUY == 0.68

    def test_exit_threshold_042(self) -> None:
        assert DEFAULT_IMBALANCE_SELL == 0.42

    def test_deadzone_no_signal(self) -> None:
        s = OrderBookImbalance()
        for imb in (0.43, 0.50, 0.60, 0.67):
            sig = s.evaluate_book(_tick(), imb)
            assert sig.action == SignalAction.HOLD

    def test_strong_imbalance_enters_with_24h_up(self) -> None:
        s = OrderBookImbalance()
        sig = s.evaluate_book(_tick(open24=99.0, last=100.0), 0.70)
        assert sig.action == SignalAction.ENTER_LONG

    def test_strong_imbalance_no_entry_when_24h_down(self) -> None:
        s = OrderBookImbalance()
        sig = s.evaluate_book(_tick(open24=101.0, last=100.0), 0.70)
        assert sig.action == SignalAction.HOLD

    def test_low_imbalance_exits(self) -> None:
        s = OrderBookImbalance()
        sig = s.evaluate_book(_tick(), 0.40)
        assert sig.action == SignalAction.EXIT
