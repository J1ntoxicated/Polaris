"""Increment 1 — WS incremental re-subscribe (frozen-socket fix).

DEMO/PAPER · flow_not_block (a FLOW INCREASE: the socket now follows focus churn).

Root cause (audit's 'frozen socket'): ``set_symbols`` / ``set_epics`` updated the
subscribed set but the base client only re-sent ``subscribe_messages`` on the NEXT
(re)connect — a HEALTHY socket that never reconnects kept its startup subscription
frozen, so a focus churn never reached the live socket. The fix sends incremental
subscribe/unsubscribe frames on the LIVE socket the instant the symbol set changes.

These tests prove: ``apply_subscription_delta`` on a CONNECTED socket emits the
venue delta frames (only added/removed); on a DISCONNECTED socket it sends nothing
(falls back to next-reconnect, the prior behaviour); and a no-change is a no-op.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from polaris.venues.alpaca.ws import AlpacaQuoteWS
from polaris.venues.capital.ws import CapitalMarketWS
from polaris.venues.okx.ws import OKXTickerWS


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, msg: str) -> None:
        self.sent.append(msg)


class _FakeSession:
    """Minimal Capital session stand-in (tokens only)."""

    class _Tok:
        cst = "cst"
        security_token = "sec"

    tokens = _Tok()

    async def ensure_tokens(self) -> Any:
        return self.tokens


def _connect(client: Any, ws: _FakeWS) -> None:
    """Simulate a live socket: seed the subscribed truth set as a real connect does."""
    client._ws = ws  # noqa: SLF001
    client.connected = True
    client._subscribed = client.current_subscription()  # noqa: SLF001


@pytest.mark.asyncio
async def test_okx_delta_on_connected_socket_emits_sub_and_unsub() -> None:
    okx = OKXTickerWS(symbols=["BTC-USDT", "ETH-USDT"], on_quote=lambda _t: None)
    ws = _FakeWS()
    _connect(okx, ws)
    okx.set_symbols(["BTC-USDT", "SOL-USDT"])  # add SOL, drop ETH
    await okx.apply_subscription_delta()
    ops = [json.loads(m) for m in ws.sent]
    sub_ids = {a["instId"] for o in ops if o.get("op") == "subscribe" for a in o["args"]}
    unsub_ids = {
        a["instId"] for o in ops if o.get("op") == "unsubscribe" for a in o["args"]
    }
    assert sub_ids == {"SOL-USDT"}
    assert unsub_ids == {"ETH-USDT"}


@pytest.mark.asyncio
async def test_okx_no_change_emits_nothing() -> None:
    okx = OKXTickerWS(symbols=["BTC-USDT"], on_quote=lambda _t: None)
    ws = _FakeWS()
    _connect(okx, ws)
    okx.set_symbols(["BTC-USDT"])
    await okx.apply_subscription_delta()
    assert ws.sent == []


@pytest.mark.asyncio
async def test_okx_delta_on_disconnected_socket_sends_nothing() -> None:
    okx = OKXTickerWS(symbols=["BTC-USDT"], on_quote=lambda _t: None)
    okx.set_symbols(["BTC-USDT", "SOL-USDT"])
    await okx.apply_subscription_delta()  # not connected → no send, no raise
    assert set(okx._symbols) == {"BTC-USDT", "SOL-USDT"}  # noqa: SLF001


def test_okx_reconnect_subscribe_uses_latest_set() -> None:
    okx = OKXTickerWS(symbols=["BTC-USDT"], on_quote=lambda _t: None)
    okx.set_symbols(["BTC-USDT", "SOL-USDT"])
    ids = {
        a["instId"]
        for m in okx.subscribe_messages()
        for a in json.loads(m)["args"]
    }
    assert ids == {"BTC-USDT", "SOL-USDT"}


@pytest.mark.asyncio
async def test_alpaca_delta_on_connected_socket_emits_sub_and_unsub() -> None:
    alp = AlpacaQuoteWS(
        symbols=["AAPL", "MSFT"], api_key="k", api_secret="s", on_quote=lambda _t: None
    )
    ws = _FakeWS()
    _connect(alp, ws)
    alp.set_symbols(["AAPL", "NVDA"])
    await alp.apply_subscription_delta()
    ops = [json.loads(m) for m in ws.sent]
    sub = next(o for o in ops if o.get("action") == "subscribe")
    unsub = next(o for o in ops if o.get("action") == "unsubscribe")
    assert sub["quotes"] == ["NVDA"]
    assert unsub["quotes"] == ["MSFT"]


@pytest.mark.asyncio
async def test_capital_delta_on_connected_socket_emits_sub_and_unsub() -> None:
    cap = CapitalMarketWS(
        epics=["EURUSD", "GBPUSD"], session=_FakeSession(), on_quote=lambda _t: None
    )
    ws = _FakeWS()
    _connect(cap, ws)
    cap.set_epics(["EURUSD", "USDJPY"])  # add USDJPY, drop GBPUSD
    await cap.apply_subscription_delta()
    ops = [json.loads(m) for m in ws.sent]
    subbed = {
        e
        for o in ops
        if o.get("destination") == "marketData.subscribe"
        for e in o["payload"]["epics"]
    }
    unsubbed = {
        e
        for o in ops
        if o.get("destination") == "marketData.unsubscribe"
        for e in o["payload"]["epics"]
    }
    assert subbed == {"USDJPY"}
    assert unsubbed == {"GBPUSD"}
