"""Venue WS client unit tests (P4 — OKX / Capital / Alpaca).

No real network. Each test drives the subclass contract:
- subscribe-message format (channel + instId/epic/symbol, chunking, auth frame),
- parse_message → QuoteTick with the venue's WS source tag (or None for control),
- gating predicates (Capital weekend/FX, Alpaca RTH),
- Capital reconnect ensure_tokens semantics (long-gap only, not steady-state).
"""

from __future__ import annotations

import json

import pytest

from polaris.venues.alpaca.ws import AlpacaQuoteWS
from polaris.venues.capital.ws import CAPITAL_EPIC_CHUNK, CapitalMarketWS
from polaris.venues.okx.ws import (
    OKXTickerWS,
    resolve_okx_ws_url,
)

# ---------------------------------------------------------------------------
# OKX
# ---------------------------------------------------------------------------


def test_resolve_okx_ws_url_us_default_maps_to_demo() -> None:
    # M3 verified: ws.us.okx.com does NOT resolve. The public tickers feed is
    # unauthenticated + region-agnostic, so the US-region demo REST host (and the
    # absent default) map to the demo public WS (wspap.okx.com).
    assert resolve_okx_ws_url(None) == "wss://wspap.okx.com:8443/ws/v5/public"
    assert (
        resolve_okx_ws_url("https://us.okx.com")
        == "wss://wspap.okx.com:8443/ws/v5/public"
    )


def test_resolve_okx_ws_url_demo_and_prod() -> None:
    assert (
        resolve_okx_ws_url("https://wspap.okx.com")
        == "wss://wspap.okx.com:8443/ws/v5/public"
    )
    assert (
        resolve_okx_ws_url("https://www.okx.com")
        == "wss://ws.okx.com:8443/ws/v5/public"
    )


def test_okx_subscribe_messages_tickers_per_inst() -> None:
    c = OKXTickerWS(symbols=["BTC-USDT", "ETH-USDT"], on_quote=lambda q: None)
    msgs = list(c.subscribe_messages())
    assert len(msgs) == 1
    payload = json.loads(msgs[0])
    assert payload["op"] == "subscribe"
    args = payload["args"]
    assert {a["channel"] for a in args} == {"tickers"}
    assert {a["instId"] for a in args} == {"BTC-USDT", "ETH-USDT"}


def test_okx_ping_is_app_level_ping_string() -> None:
    c = OKXTickerWS(symbols=["BTC-USDT"], on_quote=lambda q: None)
    # OKX wants the literal text frame "ping" (<25s) — not a protocol ws.ping().
    assert c.ping_message() == "ping"
    assert c._ping_interval < 25.0


def test_okx_parse_ticker_to_quote_tick_ws_source() -> None:
    c = OKXTickerWS(symbols=["BTC-USDT"], on_quote=lambda q: None)
    frame = json.dumps(
        {
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [
                {
                    "instId": "BTC-USDT",
                    "bidPx": "100.0",
                    "askPx": "100.2",
                    "last": "100.1",
                    "bidSz": "1.0",
                    "askSz": "2.0",
                }
            ],
        }
    )
    tick = c.parse_message(frame)
    assert tick is not None
    assert tick.instrument_id == "okx:BTC-USDT"
    assert tick.source == "okx_ws"
    assert tick.mid == pytest.approx(100.1)


def test_okx_parse_control_frames_return_none() -> None:
    c = OKXTickerWS(symbols=["BTC-USDT"], on_quote=lambda q: None)
    assert c.parse_message("pong") is None
    assert c.parse_message(json.dumps({"event": "subscribe"})) is None
    assert c.parse_message(json.dumps({"event": "error", "msg": "x"})) is None


def test_okx_resubscribe_updates_symbols() -> None:
    c = OKXTickerWS(symbols=["BTC-USDT"], on_quote=lambda q: None)
    c.set_symbols(["ETH-USDT", "SOL-USDT"])
    args = json.loads(next(iter(c.subscribe_messages())))["args"]
    assert {a["instId"] for a in args} == {"ETH-USDT", "SOL-USDT"}


# ---------------------------------------------------------------------------
# Capital
# ---------------------------------------------------------------------------


class _FakeSession:
    """Stand-in CapitalSession exposing tokens + ensure_tokens counter."""

    def __init__(self) -> None:
        self.ensure_calls = 0

        class _Tok:
            cst = "CST123"
            security_token = "SEC456"

        self._tok = _Tok()

    @property
    def tokens(self):  # noqa: ANN201
        return self._tok

    async def ensure_tokens(self):  # noqa: ANN201
        self.ensure_calls += 1
        return self._tok


def test_capital_subscribe_chunks_at_40() -> None:
    epics = [f"EPIC{i}" for i in range(95)]
    sess = _FakeSession()
    c = CapitalMarketWS(epics=epics, session=sess, on_quote=lambda q: None)
    msgs = list(c.subscribe_messages())
    # 95 epics / 40 per chunk → 3 subscribe frames.
    assert len(msgs) == 3
    parsed = [json.loads(m) for m in msgs]
    assert all(p["destination"] == "marketData.subscribe" for p in parsed)
    # Each chunk carries its CST + security token + an epics list ≤ 40.
    for p in parsed:
        assert p["correlationId"]
        assert p["cst"] == "CST123"
        assert p["securityToken"] == "SEC456"
        assert len(p["payload"]["epics"]) <= CAPITAL_EPIC_CHUNK
    flat = [e for p in parsed for e in p["payload"]["epics"]]
    assert sorted(flat) == sorted(epics)


def test_capital_parse_quote_to_tick_ws_source() -> None:
    sess = _FakeSession()
    c = CapitalMarketWS(epics=["CS.D.EURUSD.CFD.IP"], session=sess, on_quote=lambda q: None)
    frame = json.dumps(
        {
            "destination": "quote",
            "payload": {"epic": "CS.D.EURUSD.CFD.IP", "bid": 1.0, "ofr": 1.0002},
        }
    )
    tick = c.parse_message(frame)
    assert tick is not None
    assert tick.instrument_id == "capital:CS.D.EURUSD.CFD.IP"
    assert tick.source == "capital_ws"


def test_capital_parse_non_quote_returns_none() -> None:
    sess = _FakeSession()
    c = CapitalMarketWS(epics=["E"], session=sess, on_quote=lambda q: None)
    assert c.parse_message(json.dumps({"destination": "ping"})) is None
    assert c.parse_message(json.dumps({"destination": "marketData.subscribe"})) is None


def test_capital_ping_under_10min() -> None:
    sess = _FakeSession()
    c = CapitalMarketWS(epics=["E"], session=sess, on_quote=lambda q: None)
    # Keepalive must be < 10 min so the token never idle-expires steady-state.
    assert c._ping_interval < 600.0
    p = json.loads(c.ping_message())
    assert p["destination"] == "ping"
    assert p["cst"] == "CST123"


async def test_capital_open_steady_state_no_ensure_tokens() -> None:
    """Steady-state reconnect (recent activity) must NOT re-auth (M4)."""
    sess = _FakeSession()
    c = CapitalMarketWS(epics=["E"], session=sess, on_quote=lambda q: None)
    # Simulate a connection that just had activity (idle < 10 min).
    c._mark_activity()
    await c._ensure_session_fresh()
    assert sess.ensure_calls == 0


async def test_capital_long_gap_calls_ensure_tokens() -> None:
    """>10-min idle before (re)connect → ensure_tokens once (M4)."""
    sess = _FakeSession()
    c = CapitalMarketWS(epics=["E"], session=sess, on_quote=lambda q: None)
    # No prior activity → treated as a long-gap (re)connect.
    await c._ensure_session_fresh()
    assert sess.ensure_calls == 1


def test_capital_is_gated_weekend() -> None:
    sess = _FakeSession()
    c = CapitalMarketWS(epics=["E"], session=sess, on_quote=lambda q: None)
    # Saturday 2026-06-06 12:00 UTC.
    assert c._is_weekend(ts=1780747200) is True
    # Wednesday 2026-06-03 12:00 UTC.
    assert c._is_weekend(ts=1780488000) is False


# ---------------------------------------------------------------------------
# Alpaca
# ---------------------------------------------------------------------------


def test_alpaca_ws_url_iex() -> None:
    c = AlpacaQuoteWS(
        symbols=["AAPL"], api_key="k", api_secret="s", on_quote=lambda q: None
    )
    assert c.ws_url == "wss://stream.data.alpaca.markets/v2/iex"


def test_alpaca_subscribe_auth_then_quotes_trades() -> None:
    c = AlpacaQuoteWS(
        symbols=["AAPL", "MSFT"], api_key="KEY", api_secret="SEC", on_quote=lambda q: None
    )
    msgs = list(c.subscribe_messages())
    assert len(msgs) == 2
    auth = json.loads(msgs[0])
    assert auth == {"action": "auth", "key": "KEY", "secret": "SEC"}
    sub = json.loads(msgs[1])
    assert sub["action"] == "subscribe"
    assert sorted(sub["quotes"]) == ["AAPL", "MSFT"]
    assert sorted(sub["trades"]) == ["AAPL", "MSFT"]


def test_alpaca_parse_quote_array_to_tick() -> None:
    c = AlpacaQuoteWS(symbols=["AAPL"], api_key="k", api_secret="s", on_quote=lambda q: None)
    # Alpaca sends an ARRAY of messages per frame.
    frame = json.dumps([{"T": "q", "S": "AAPL", "bp": 100.0, "ap": 100.2, "bs": 1, "as": 2}])
    tick = c.parse_message(frame)
    assert tick is not None
    assert tick.instrument_id == "alpaca:AAPL"
    assert tick.source == "alpaca_ws"


def test_alpaca_parse_control_messages_none() -> None:
    c = AlpacaQuoteWS(symbols=["AAPL"], api_key="k", api_secret="s", on_quote=lambda q: None)
    assert c.parse_message(json.dumps([{"T": "success", "msg": "authenticated"}])) is None
    assert c.parse_message(json.dumps([{"T": "subscription"}])) is None


def test_alpaca_is_gated_outside_rth() -> None:
    c = AlpacaQuoteWS(symbols=["AAPL"], api_key="k", api_secret="s", on_quote=lambda q: None)
    # Saturday → closed → gated.
    assert c._gated_at(ts=1780747200) is True
    # Wednesday 2026-06-03 17:00 UTC = 13:00 ET → RTH → not gated.
    assert c._gated_at(ts=1780506000) is False
