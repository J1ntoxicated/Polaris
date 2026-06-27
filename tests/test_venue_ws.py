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


def test_resolve_okx_ws_url_us_default_maps_to_us_demo() -> None:
    # Jin official reference: us.okx.com → wsuspap.okx.com (US-region demo WS,
    # live-verified BTC-USDT ticker, region-consistent with the REST host). The
    # global-demo wspap.okx.com is NOT the US endpoint; ws.us.okx.com does not
    # exist but wsuspap.okx.com does.
    assert resolve_okx_ws_url(None) == "wss://wsuspap.okx.com:8443/ws/v5/public"
    assert (
        resolve_okx_ws_url("https://us.okx.com")
        == "wss://wsuspap.okx.com:8443/ws/v5/public"
    )


def test_resolve_okx_ws_url_us_subdomain_maps_to_us_demo() -> None:
    # A us.okx.com sub-domain (REST resolver permits *.us.okx.com) stays on the
    # US demo WS — never silently downgraded to the global demo.
    assert (
        resolve_okx_ws_url("https://aws.us.okx.com")
        == "wss://wsuspap.okx.com:8443/ws/v5/public"
    )


def test_resolve_okx_ws_url_global_demo_and_prod() -> None:
    # An explicit GLOBAL demo REST host stays on the global demo WS; only the
    # international prod host (www.okx.com) maps to the prod WS.
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


def test_alpaca_ws_url_sip_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default feed is SIP (paid real-time, no symbol cap) — Jin's entitlement.
    monkeypatch.delenv("POLARIS_ALPACA_FEED", raising=False)
    c = AlpacaQuoteWS(
        symbols=["AAPL"], api_key="k", api_secret="s", on_quote=lambda q: None
    )
    assert c.ws_url == "wss://stream.data.alpaca.markets/v2/sip"


def test_alpaca_ws_url_iex_when_env_iex(monkeypatch: pytest.MonkeyPatch) -> None:
    # POLARIS_ALPACA_FEED=iex pins the free fallback feed (case-insensitive).
    monkeypatch.setenv("POLARIS_ALPACA_FEED", "IEX")
    c = AlpacaQuoteWS(
        symbols=["AAPL"], api_key="k", api_secret="s", on_quote=lambda q: None
    )
    assert c.ws_url == "wss://stream.data.alpaca.markets/v2/iex"


def test_alpaca_ws_url_garbage_env_falls_back_to_sip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_ALPACA_FEED", "nonsense")
    c = AlpacaQuoteWS(
        symbols=["AAPL"], api_key="k", api_secret="s", on_quote=lambda q: None
    )
    assert c.ws_url == "wss://stream.data.alpaca.markets/v2/sip"


def test_alpaca_runtime_downgrade_on_auth_error_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SIP key without entitlement → an auth/subscription error frame downgrades the
    # live feed to IEX (next reconnect uses the IEX URL). Graceful, never a halt.
    monkeypatch.delenv("POLARIS_ALPACA_FEED", raising=False)
    c = AlpacaQuoteWS(
        symbols=["AAPL"], api_key="k", api_secret="s", on_quote=lambda q: None
    )
    assert c.ws_url.endswith("/sip")
    err = json.dumps([{"T": "error", "code": 409, "msg": "insufficient subscription"}])
    assert c.parse_message(err) is None  # control frame → no tick
    assert c.ws_url == "wss://stream.data.alpaca.markets/v2/iex"  # downgraded


def test_alpaca_runtime_downgrade_recaps_subscribe_to_30(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #43: the configured feed is SIP (no cap), so the FIRST subscribe sends the
    # full set. A runtime entitlement error frame downgrades the live feed to IEX;
    # the NEXT subscribe (reconnect) MUST re-cap to 30 — otherwise the IEX socket
    # re-trips "symbol limit exceeded". (The env is unset → configured SIP, so this
    # exercises the RUNTIME downgrade path, not the env-pinned-IEX path.)
    from polaris.core.universe.schema import (
        reset_alpaca_runtime_feed,
        ws_budget_for_venue,
    )

    monkeypatch.delenv("POLARIS_ALPACA_FEED", raising=False)
    reset_alpaca_runtime_feed()
    syms = [f"SYM{i}" for i in range(40)]
    c = AlpacaQuoteWS(
        symbols=syms, api_key="k", api_secret="s", on_quote=lambda q: None
    )
    try:
        # Pre-downgrade: SIP sends all 40 + the schema budget is the SIP default.
        sub0 = json.loads(list(c.subscribe_messages())[1])
        assert len(sub0["quotes"]) == 40
        assert ws_budget_for_venue("alpaca") == 60
        # Runtime entitlement failure → downgrade.
        c.parse_message(
            json.dumps([{"T": "error", "code": 409, "msg": "insufficient subscription"}])
        )
        # Post-downgrade: the re-subscribe (next reconnect) is capped at 30 AND the
        # schema-level WS budget now tracks IEX (30) — so the focus partition seats
        # 30, not 60, on the IEX socket.
        sub1 = json.loads(list(c.subscribe_messages())[1])
        assert len(sub1["quotes"]) == 30
        assert sub1["quotes"] == syms[:30]  # focus order preserved (Tier-S leads)
        assert c.current_subscription() == set(syms[:30])
        assert ws_budget_for_venue("alpaca") == 30
    finally:
        reset_alpaca_runtime_feed()


def test_alpaca_non_entitlement_error_keeps_sip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A non-auth error (e.g. a malformed-frame complaint) must NOT downgrade SIP.
    monkeypatch.delenv("POLARIS_ALPACA_FEED", raising=False)
    c = AlpacaQuoteWS(
        symbols=["AAPL"], api_key="k", api_secret="s", on_quote=lambda q: None
    )
    c.parse_message(json.dumps([{"T": "error", "code": 400, "msg": "invalid syntax"}]))
    assert c.ws_url.endswith("/sip")  # unrelated error → stays on SIP


def test_alpaca_iex_caps_subscribe_at_30(monkeypatch: pytest.MonkeyPatch) -> None:
    # On IEX the subscribe frame is hard-capped at 30 symbols (focus order kept),
    # so a >30 set never trips "symbol limit exceeded". SIP would send all of them.
    monkeypatch.setenv("POLARIS_ALPACA_FEED", "iex")
    syms = [f"SYM{i}" for i in range(40)]
    c = AlpacaQuoteWS(
        symbols=syms, api_key="k", api_secret="s", on_quote=lambda q: None
    )
    sub = json.loads(list(c.subscribe_messages())[1])
    assert len(sub["quotes"]) == 30
    assert sub["quotes"] == syms[:30]  # focus order preserved (Tier-S leads)
    assert c.current_subscription() == set(syms[:30])


def test_alpaca_sip_does_not_cap_subscribe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_ALPACA_FEED", raising=False)
    syms = [f"SYM{i}" for i in range(40)]
    c = AlpacaQuoteWS(
        symbols=syms, api_key="k", api_secret="s", on_quote=lambda q: None
    )
    sub = json.loads(list(c.subscribe_messages())[1])
    assert len(sub["quotes"]) == 40  # SIP has no symbol cap


def test_alpaca_subscribe_auth_then_quotes_only() -> None:
    # Quotes ONLY — parse_message discards every non-"q" frame, so a "trades"
    # subscription was pure waste AND doubled the IEX subscription count past
    # the free-feed 30-symbol cap ("symbol limit exceeded").
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
    assert "trades" not in sub  # the symbol-limit-doubling waste is gone


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
