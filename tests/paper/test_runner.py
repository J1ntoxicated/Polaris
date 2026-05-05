"""tests/paper/test_runner.py — Runner state I/O + cycle (mocked OKX)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy
from src.paper import runner as paper_runner
from src.paper.state import PaperBalance, Position


class StubStrategy(Strategy):
    name = "stub"
    min_window = 1

    def __init__(self, action: SignalAction, target_size: float = 1000.0):
        self.action_to_emit = action
        self.target_size = target_size

    def evaluate(self, window):
        ts = window[-1].timestamp_ms
        if self.action_to_emit == SignalAction.HOLD:
            return Signal(timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0)
        if self.action_to_emit == SignalAction.EXIT:
            return Signal(timestamp_ms=ts, action=SignalAction.EXIT, confidence=1.0)
        return Signal(
            timestamp_ms=ts,
            action=self.action_to_emit,
            confidence=0.8,
            target_size_usd=self.target_size,
        )


def make_candles(prices: list[float]) -> list[Candle]:
    return [
        Candle(timestamp_ms=(i + 1) * 86_400_000, open=p, high=p * 1.01, low=p * 0.99, close=p, volume=100)
        for i, p in enumerate(prices)
    ]


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_runner, "DEFAULT_STATE_DIR", tmp_path)
    log_dir = tmp_path / "log"
    monkeypatch.setattr(paper_runner.paper_logger, "LOG_DIR", log_dir)
    # Phase 16: disable SQL ledger primary-read for these tests so each runs
    # against a fresh JSON-only state (else SQL holds prod-leftover positions).
    monkeypatch.setattr(paper_runner, "_LEDGER_ENABLED", False)
    return tmp_path


class TestStateIO:
    def test_load_initial(self, tmp_state) -> None:
        b = paper_runner.load_state("BTC-USDT", "stub", starting_usd=10000)
        assert b.starting_usd == 10000
        assert b.cash_usd == 10000
        assert b.n_open == 0

    def test_save_and_load_roundtrip(self, tmp_state) -> None:
        b = PaperBalance(starting_usd=5000, cash_usd=5000)
        p = Position("BTC-1", "BTC-USDT", 1, 50000, 1000, 1)
        b = b.open(p)
        paper_runner.save_state("BTC-USDT", "stub", b)
        b2 = paper_runner.load_state("BTC-USDT", "stub")
        assert b2.cash_usd == 4000
        assert b2.n_open == 1
        assert b2.open_positions[0].position_id == "BTC-1"


class TestRunCycle:
    def test_enter_long(self, tmp_state) -> None:
        candles = make_candles([100, 110, 120])
        with patch.object(paper_runner, "fetch_history", return_value=candles):
            strategy = StubStrategy(SignalAction.ENTER_LONG, target_size=500)
            summary = paper_runner.run_cycle(
                ticker="TEST-USDT", strategy=strategy, bar="1D", starting_usd=5000,
            )
        assert summary["signal"] == "enter_long"
        assert summary["n_open_post"] == 1
        # max_position_pct 0.02 × 5000 = 100, target 500 → min 100
        assert summary["cash_usd"] == pytest.approx(5000 - 100)

    def test_hold_no_change(self, tmp_state) -> None:
        candles = make_candles([100, 110, 120])
        with patch.object(paper_runner, "fetch_history", return_value=candles):
            strategy = StubStrategy(SignalAction.HOLD)
            summary = paper_runner.run_cycle(
                ticker="TEST-USDT", strategy=strategy, bar="1D", starting_usd=5000,
            )
        assert summary["signal"] == "hold"
        assert summary["n_open_post"] == 0
        assert summary["cash_usd"] == 5000

    def test_exit_after_entry(self, tmp_state) -> None:
        # Cycle 1: ENTER
        candles_1 = make_candles([100])
        with patch.object(paper_runner, "fetch_history", return_value=candles_1):
            paper_runner.run_cycle(
                ticker="TEST-USDT",
                strategy=StubStrategy(SignalAction.ENTER_LONG, target_size=100),
                bar="1D", starting_usd=5000,
            )
        # Cycle 2: EXIT (price up)
        candles_2 = make_candles([100, 120])
        with patch.object(paper_runner, "fetch_history", return_value=candles_2):
            summary = paper_runner.run_cycle(
                ticker="TEST-USDT",
                strategy=StubStrategy(SignalAction.EXIT),
                bar="1D", starting_usd=5000,
            )
        assert summary["n_closed"] == 1
        assert summary["realized_pnl_usd"] > 0  # +20% gross - 1.4% fee = +18.6%

    def test_no_candles_error(self, tmp_state) -> None:
        with patch.object(paper_runner, "fetch_history", return_value=[]):
            summary = paper_runner.run_cycle(
                ticker="TEST-USDT",
                strategy=StubStrategy(SignalAction.HOLD),
                bar="1D",
            )
        assert summary["status"] == "error"
