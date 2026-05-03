"""tests/data/test_binance_ws.py — Binance WS parsing + whitelist (Phase 2g Round 3 Codex fix).

TDD: Fix 3 (BINANCE_SPOT_SYMBOLS whitelist) + Q5 (WS parsing edge cases).
"""
from __future__ import annotations

import asyncio
import time

import pytest

import src.data.binance_ws as bws


# ── Helpers ──────────────────────────────────────────────────────────────────


def _clear_stores() -> None:
    bws._trade_store.clear()
    bws._book_ticker_store.clear()


# ── Fix 3: BINANCE_SPOT_SYMBOLS whitelist ─────────────────────────────────────


class TestBuildStreams:
    """_build_streams must skip unsupported symbols (Fix 3)."""

    def test_supported_symbols_included(self) -> None:
        """BTC-USDT, ETH-USDT → streams returned."""
        streams = bws._build_streams(["BTC-USDT", "ETH-USDT"])
        assert "btcusdt@trade" in streams
        assert "btcusdt@bookTicker" in streams
        assert "ethusdt@trade" in streams
        assert "ethusdt@bookTicker" in streams

    def test_unsupported_symbol_skipped(self) -> None:
        """TRUMP-USDT not in whitelist → zero streams for that symbol."""
        streams = bws._build_streams(["TRUMP-USDT"])
        trump_streams = [s for s in streams if "trump" in s]
        assert trump_streams == [], f"Expected no TRUMP streams, got: {trump_streams}"

    def test_ordi_usdt_skipped(self) -> None:
        """ORDI-USDT not in Binance major whitelist → skipped."""
        streams = bws._build_streams(["ORDI-USDT"])
        ordi_streams = [s for s in streams if "ordi" in s]
        assert ordi_streams == [], f"Expected no ORDI streams, got: {ordi_streams}"

    def test_mixed_symbols_only_supported_returned(self) -> None:
        """Mixed list: supported are included, unsupported skipped."""
        streams = bws._build_streams(["BTC-USDT", "TRUMP-USDT", "ETH-USDT", "ORDI-USDT"])
        syms_in_streams = {s.split("@")[0] for s in streams}
        assert "btcusdt" in syms_in_streams
        assert "ethusdt" in syms_in_streams
        assert "trumpusdt" not in syms_in_streams
        assert "ordiusdt" not in syms_in_streams

    def test_whitelist_constant_exists(self) -> None:
        """BINANCE_SPOT_SYMBOLS frozenset must be exported."""
        assert hasattr(bws, "BINANCE_SPOT_SYMBOLS"), "BINANCE_SPOT_SYMBOLS not found"
        assert isinstance(bws.BINANCE_SPOT_SYMBOLS, frozenset)
        assert "BTCUSDT" in bws.BINANCE_SPOT_SYMBOLS
        assert "ETHUSDT" in bws.BINANCE_SPOT_SYMBOLS
        assert "SOLUSDT" in bws.BINANCE_SPOT_SYMBOLS

    def test_empty_input_returns_empty(self) -> None:
        streams = bws._build_streams([])
        assert streams == []

    def test_all_unsupported_returns_empty(self) -> None:
        streams = bws._build_streams(["TRUMP-USDT", "ORDI-USDT"])
        assert streams == []


# ── Q5: _handle_trade m-flag semantics ───────────────────────────────────────


class TestHandleTrade:
    """m=True → seller aggressor (sell side). m=False → buyer aggressor (buy side)."""

    def setup_method(self) -> None:
        _clear_stores()

    def test_m_true_is_sell_aggressor(self) -> None:
        """Binance m=True means buyer is maker → sell aggressor."""
        msg = {"e": "trade", "s": "BTCUSDT", "T": 1_700_000_000_000,
               "p": "79000.0", "q": "1.5", "m": True}
        bws._handle_trade(msg)
        trade = bws.get_last_trade("BTCUSDT")
        assert trade is not None
        assert trade[1] == "sell", f"Expected sell, got {trade[1]}"

    def test_m_false_is_buy_aggressor(self) -> None:
        """Binance m=False means buyer is taker → buy aggressor."""
        msg = {"e": "trade", "s": "BTCUSDT", "T": 1_700_000_000_001,
               "p": "79100.0", "q": "0.8", "m": False}
        bws._handle_trade(msg)
        trade = bws.get_last_trade("BTCUSDT")
        assert trade is not None
        assert trade[1] == "buy", f"Expected buy, got {trade[1]}"

    def test_missing_fields_silently_dropped(self) -> None:
        """Malformed message (missing T field) → store unchanged."""
        _clear_stores()
        msg = {"e": "trade", "s": "BTCUSDT", "p": "79000.0", "q": "1.0"}
        bws._handle_trade(msg)
        assert bws.get_last_trade("BTCUSDT") is None

    def test_invalid_price_dropped(self) -> None:
        """Non-numeric price → store unchanged."""
        msg = {"e": "trade", "s": "ETHUSDT", "T": 1_700_000_000_000,
               "p": "not_a_number", "q": "1.0", "m": False}
        bws._handle_trade(msg)
        assert bws.get_last_trade("ETHUSDT") is None

    def test_symbol_uppercased(self) -> None:
        """Symbol stored uppercase regardless of input."""
        msg = {"e": "trade", "s": "btcusdt", "T": 1_700_000_000_000,
               "p": "79000.0", "q": "1.0", "m": False}
        bws._handle_trade(msg)
        assert bws.get_last_trade("BTCUSDT") is not None
        assert bws.get_last_trade("btcusdt") is not None  # case-insensitive accessor


# ── Q5: _on_message bookTicker detection ─────────────────────────────────────


class TestOnMessage:
    """bookTicker raw stream has no 'e' field — detect by {u, b, a} fields."""

    def setup_method(self) -> None:
        _clear_stores()

    def test_book_ticker_without_e_field_parsed(self) -> None:
        """Raw bookTicker (no 'e' field) detected via {u, b, a} heuristic."""
        raw = '{"u":100,"s":"BTCUSDT","b":"79000.0","B":"2.5","a":"79001.0","A":"1.8"}'
        asyncio.run(bws._on_message(raw, None))
        bt = bws.get_book_ticker("BTCUSDT")
        assert bt is not None
        assert bt["bid"] == pytest.approx(79000.0)
        assert bt["ask"] == pytest.approx(79001.0)

    def test_trade_event_parsed(self) -> None:
        """Standard @trade event with 'e' field parsed correctly."""
        raw = '{"e":"trade","s":"ETHUSDT","T":1700000000000,"p":"3000.0","q":"0.5","m":false}'
        asyncio.run(bws._on_message(raw, None))
        trade = bws.get_last_trade("ETHUSDT")
        assert trade is not None
        assert trade[1] == "buy"

    def test_subscription_ack_ignored(self) -> None:
        """Subscription ack {result: null, id: ...} → no state change."""
        raw = '{"result":null,"id":12345}'
        asyncio.run(bws._on_message(raw, None))
        # Nothing stored — no crash
        assert bws.get_last_trade("BTCUSDT") is None

    def test_invalid_json_ignored(self) -> None:
        """Invalid JSON → no crash."""
        asyncio.run(bws._on_message("NOT_JSON{{", None))


# ── Q5: compute_taker_buy_ratio edge cases ────────────────────────────────────


class TestComputeTakerBuyRatio:
    """Edge cases: empty buffer, all-sell, all-buy."""

    def setup_method(self) -> None:
        _clear_stores()

    def test_empty_buffer_returns_none(self) -> None:
        result = bws.compute_taker_buy_ratio("BTCUSDT", window=100)
        assert result is None

    def test_all_sell_returns_zero(self) -> None:
        """All sell trades → ratio = 0.0."""
        from collections import deque
        buf = deque(maxlen=200)
        for i in range(10):
            buf.append((1_700_000_000_000 + i, "sell", 1.0, 79000.0))
        bws._trade_store["BTCUSDT"] = buf
        result = bws.compute_taker_buy_ratio("BTCUSDT", window=100)
        assert result == pytest.approx(0.0)

    def test_all_buy_returns_one(self) -> None:
        """All buy trades → ratio = 1.0."""
        from collections import deque
        buf = deque(maxlen=200)
        for i in range(10):
            buf.append((1_700_000_000_000 + i, "buy", 2.0, 79000.0))
        bws._trade_store["BTCUSDT"] = buf
        result = bws.compute_taker_buy_ratio("BTCUSDT", window=100)
        assert result == pytest.approx(1.0)

    def test_mixed_trades_weighted_by_size(self) -> None:
        """3 buy @ size 2 + 1 sell @ size 2 → ratio = 0.75."""
        from collections import deque
        buf = deque(maxlen=200)
        buf.append((1_700_000_000_001, "buy", 2.0, 79000.0))
        buf.append((1_700_000_000_002, "buy", 2.0, 79000.0))
        buf.append((1_700_000_000_003, "buy", 2.0, 79000.0))
        buf.append((1_700_000_000_004, "sell", 2.0, 79000.0))
        bws._trade_store["BTCUSDT"] = buf
        result = bws.compute_taker_buy_ratio("BTCUSDT", window=100)
        assert result == pytest.approx(0.75)

    def test_window_slices_recent_trades(self) -> None:
        """window=2: only last 2 trades count."""
        from collections import deque
        buf = deque(maxlen=200)
        # 5 sells + 2 buys at end
        for i in range(5):
            buf.append((1_700_000_000_000 + i, "sell", 1.0, 79000.0))
        buf.append((1_700_000_000_005, "buy", 1.0, 79000.0))
        buf.append((1_700_000_000_006, "buy", 1.0, 79000.0))
        bws._trade_store["BTCUSDT"] = buf
        result = bws.compute_taker_buy_ratio("BTCUSDT", window=2)
        assert result == pytest.approx(1.0)  # last 2 are buys
