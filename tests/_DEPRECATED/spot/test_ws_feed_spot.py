import time
from invasion.spot import ws_feed_spot


def test_parse_ticker_tolerates_empty_strings():
    # OKX sends bidPx="" for some illiquid pairs — must not crash.
    msg = {
        "arg": {"channel": "tickers", "instId": "OBSC-USDT"},
        "data": [{"instId": "OBSC-USDT", "last": "0.001",
                   "bidPx": "", "askPx": "", "ts": ""}],
    }
    upd = ws_feed_spot.parse_message(msg)
    assert upd is not None
    assert upd["bid"] == 0.0
    assert upd["ask"] == 0.0
    assert upd["ts_ms"] == 0


def test_parse_books_tolerates_empty_strings():
    msg = {
        "arg": {"channel": "books5", "instId": "OBSC-USDT"},
        "data": [{
            "asks": [["", "", "0", "1"]],
            "bids": [["", "", "0", "1"]],
            "ts": "",
        }],
    }
    upd = ws_feed_spot.parse_message(msg)
    assert upd is not None
    assert upd["best_ask"] == 0.0
    assert upd["best_bid"] == 0.0


def test_parse_ticker_message():
    msg = {
        "arg": {"channel": "tickers", "instId": "BTC-USDT"},
        "data": [{"instId": "BTC-USDT", "last": "50000.5",
                  "bidPx": "50000", "askPx": "50001",
                  "ts": "1700000000000"}],
    }
    upd = ws_feed_spot.parse_message(msg)
    assert upd == {
        "channel": "ticker", "ticker": "BTC",
        "last": 50000.5, "bid": 50000.0, "ask": 50001.0,
        "ts_ms": 1700000000000,
    }


def test_parse_books5_message():
    msg = {
        "arg": {"channel": "books5", "instId": "ETH-USDT"},
        "data": [{
            "asks": [["2500.0", "1.5", "0", "1"]],
            "bids": [["2499.5", "2.0", "0", "1"]],
            "ts": "1700000000000",
        }],
    }
    upd = ws_feed_spot.parse_message(msg)
    assert upd["channel"] == "book"
    assert upd["ticker"] == "ETH"
    assert upd["best_bid"] == 2499.5
    assert upd["best_ask"] == 2500.0
    assert upd["bid_depth"] == 2.0
    assert upd["ask_depth"] == 1.5


def test_parse_trades_message():
    msg = {
        "arg": {"channel": "trades", "instId": "SOL-USDT"},
        "data": [
            {"instId": "SOL-USDT", "side": "buy", "sz": "10.0",
             "px": "100.5", "ts": "1700000000000"},
            {"instId": "SOL-USDT", "side": "sell", "sz": "5.0",
             "px": "100.4", "ts": "1700000001000"},
        ],
    }
    upd = ws_feed_spot.parse_message(msg)
    assert upd["channel"] == "trades"
    assert upd["ticker"] == "SOL"
    assert len(upd["trades"]) == 2
    assert upd["trades"][0] == {"side": "buy", "sz": 10.0,
                                  "px": 100.5, "ts_ms": 1700000000000}


def test_parse_subscribe_ack_returns_none():
    msg = {"event": "subscribe", "arg": {}}
    assert ws_feed_spot.parse_message(msg) is None


def test_parse_unknown_channel_returns_none():
    msg = {"arg": {"channel": "weird", "instId": "BTC-USDT"}, "data": []}
    assert ws_feed_spot.parse_message(msg) is None


def test_state_apply_ticker():
    state = ws_feed_spot.State()
    state.apply({"channel": "ticker", "ticker": "BTC",
                  "last": 50000.0, "bid": 49999.5, "ask": 50000.5,
                  "ts_ms": 1700000000000})
    assert state.get_ticker("BTC")["last"] == 50000.0


def test_state_apply_books_freshness():
    state = ws_feed_spot.State()
    state.apply({"channel": "book", "ticker": "ETH",
                  "best_bid": 2499.5, "best_ask": 2500.0,
                  "bid_depth": 2.0, "ask_depth": 1.5,
                  "ts_ms": int(time.time() * 1000)})
    book = state.get_book("ETH")
    assert book["best_bid"] == 2499.5
    assert state.book_age_ms("ETH") < 1000


def test_state_taker_flow_window():
    state = ws_feed_spot.State()
    now = int(time.time() * 1000)
    state.apply({"channel": "trades", "ticker": "SOL", "trades": [
        {"side": "buy", "sz": 10.0, "px": 100.0, "ts_ms": now - 30000},
        {"side": "sell", "sz": 5.0, "px": 99.9, "ts_ms": now - 20000},
        {"side": "buy", "sz": 2.0, "px": 100.1, "ts_ms": now - 90000},
    ]})
    flow = state.get_taker_flow("SOL", window_s=60)
    assert flow["buy_sz"] == 10.0
    assert flow["sell_sz"] == 5.0  # 90s old trade excluded


import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_ws_run_processes_message():
    """Run loop reads from mock socket, applies to State."""
    fake_msgs = [
        json.dumps({
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [{"instId": "BTC-USDT", "last": "50000",
                       "bidPx": "49999", "askPx": "50001",
                       "ts": "1700000000000"}],
        }),
        # signal stop
        None,
    ]
    mock_ws = AsyncMock()

    async def _recv():
        m = fake_msgs.pop(0)
        if m is None:
            raise asyncio.CancelledError()
        return m
    mock_ws.recv = _recv
    mock_ws.send = AsyncMock()

    state = ws_feed_spot.State()
    feed = ws_feed_spot.OKXSpotWSFeed(state, ws_factory=lambda: _AsyncCM(mock_ws))
    await feed._run_once(["BTC-USDT"])
    assert state.get_ticker("BTC")["last"] == 50000.0


class _AsyncCM:
    def __init__(self, ws): self.ws = ws
    async def __aenter__(self): return self.ws
    async def __aexit__(self, *a): return None
