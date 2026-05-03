"""tests/paper/test_state.py — Pure paper state."""
from __future__ import annotations

import pytest

from src.paper.state import PaperBalance, Position, PositionStatus


class TestPosition:
    def test_open_long_valid(self) -> None:
        p = Position("BTC-1", "BTC-USDT", 1, 50000.0, 1000.0, 1_700_000_000_000)
        assert p.is_open
        assert p.gross_pct == 0.0
        assert p.net_pct == 0.0

    def test_invalid_size(self) -> None:
        with pytest.raises(ValueError, match="size_usd"):
            Position("X", "BTC", 1, 50000, 0, 1)

    def test_invalid_direction(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            Position("X", "BTC", 0, 50000, 1000, 1)

    def test_close_long_profit(self) -> None:
        p = Position("X", "BTC", 1, 50000, 1000, 1)
        c = p.close(exit_price=55000, close_ts_ms=2)
        assert not c.is_open
        assert c.gross_pct == pytest.approx(0.10)
        assert c.net_pct == pytest.approx(0.10 - 0.014)
        assert c.net_usd == pytest.approx(1000 * (0.10 - 0.014))

    def test_close_invalid_ts(self) -> None:
        p = Position("X", "BTC", 1, 50000, 1000, 100)
        with pytest.raises(ValueError, match="close_ts_ms"):
            p.close(exit_price=50000, close_ts_ms=50)

    def test_immutable(self) -> None:
        p = Position("X", "BTC", 1, 50000, 1000, 1)
        with pytest.raises((AttributeError, TypeError)):
            p.entry_price = 60000  # type: ignore[misc]


class TestPaperBalance:
    def test_initial(self) -> None:
        b = PaperBalance(starting_usd=5000, cash_usd=5000)
        assert b.n_open == 0
        assert b.n_closed == 0
        assert b.realized_pnl_usd == 0.0

    def test_open_position(self) -> None:
        b = PaperBalance(starting_usd=5000, cash_usd=5000)
        p = Position("BTC-1", "BTC-USDT", 1, 50000, 1000, 1)
        b2 = b.open(p)
        assert b2.cash_usd == 4000
        assert b2.n_open == 1

    def test_open_insufficient_cash(self) -> None:
        b = PaperBalance(starting_usd=5000, cash_usd=500)
        p = Position("BTC-1", "BTC-USDT", 1, 50000, 1000, 1)
        with pytest.raises(ValueError, match="insufficient cash"):
            b.open(p)

    def test_close_position_profit(self) -> None:
        b = PaperBalance(starting_usd=5000, cash_usd=5000)
        p = Position("BTC-1", "BTC-USDT", 1, 50000, 1000, 1)
        b = b.open(p)
        b = b.close("BTC-1", exit_price=55000, close_ts_ms=2)
        # cash = 4000 (after open) + 1000 (size returned) + (1000 × (0.10 - 0.014))
        assert b.cash_usd == pytest.approx(4000 + 1000 + 1000 * 0.086)
        assert b.n_open == 0
        assert b.n_closed == 1
        assert b.realized_pnl_usd == pytest.approx(1000 * 0.086)

    def test_close_not_found(self) -> None:
        b = PaperBalance(starting_usd=5000, cash_usd=5000)
        with pytest.raises(ValueError, match="not found"):
            b.close("missing", 50000, 100)

    def test_equity_open_position(self) -> None:
        b = PaperBalance(starting_usd=5000, cash_usd=5000)
        p = Position("BTC-1", "BTC-USDT", 1, 50000, 1000, 1)
        b = b.open(p)
        # current price 55000 → unrealized +10%, market value 1100
        eq = b.equity_usd({"BTC-USDT": 55000})
        assert eq == pytest.approx(4000 + 1100)

    def test_equity_no_price(self) -> None:
        # 가격 없으면 entry_price 사용 (보수적)
        b = PaperBalance(starting_usd=5000, cash_usd=5000)
        p = Position("BTC-1", "BTC-USDT", 1, 50000, 1000, 1)
        b = b.open(p)
        eq = b.equity_usd({})
        assert eq == pytest.approx(5000)
