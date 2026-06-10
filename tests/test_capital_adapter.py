"""Capital CFD adapter — session login, anti-idle ping, position lifecycle."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from polaris.venues.capital.adapter import (
    CAPITAL_CONFIRMS_PATH,
    CAPITAL_POSITIONS_PATH,
    CAPITAL_WORKINGORDERS_PATH,
    CapitalAdapter,
    CapitalDealResponse,
)
from polaris.venues.capital.constraint_translator import (
    CapitalMarketConstraint,
    round_size_to_step,
    size_usd_to_lots,
)
from polaris.venues.capital.session import (
    CAPITAL_PING_PATH,
    CAPITAL_SESSION_PATH,
    TOKEN_REFRESH_DEADLINE_SEC,
    CapitalSession,
)


class _Responder:
    """Configurable mock responder that records every call."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.handlers: dict[tuple[str, str], Any] = {}
        self.login_count = 0

    def on(self, method: str, path: str, handler: Any) -> None:
        self.handlers[(method.upper(), path)] = handler

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        key = (req.method.upper(), req.url.path)
        handler = self.handlers.get(key)
        if handler is None:
            return httpx.Response(404, json={"errorCode": "not.found"})
        if callable(handler):
            return handler(req)
        return httpx.Response(200, json=handler)


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responder: _Responder) -> None:
        self.responder = responder

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        out = self.responder(request)
        if isinstance(out, httpx.Response):
            return out
        return httpx.Response(200, json=out)


def _login_handler(responder: _Responder, *, account_id: str = "316697823116874014"):
    def _h(_req: httpx.Request) -> httpx.Response:
        responder.login_count += 1
        return httpx.Response(
            200,
            json={"currentAccountId": account_id},
            headers={
                "CST": f"cst-token-{responder.login_count}",
                "X-SECURITY-TOKEN": f"sec-token-{responder.login_count}",
            },
        )

    return _h


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_login_cst_security_token() -> None:
    responder = _Responder()
    responder.on("POST", CAPITAL_SESSION_PATH, _login_handler(responder))
    client = httpx.AsyncClient(
        transport=_MockTransport(responder), base_url="https://demo-api"
    )
    sess = CapitalSession(
        api_key="K", identifier="e@x", password="pw", client=client, auto_ping=False
    )
    try:
        toks = await sess.login()
        assert toks.cst.startswith("cst-token-")
        assert toks.security_token.startswith("sec-token-")
        assert toks.account_id == "316697823116874014"
        assert toks.deadline_ts > time.time()
    finally:
        await sess.aclose()
        await client.aclose()


@pytest.mark.asyncio
async def test_login_auto_starts_ping_loop() -> None:
    """Codex Day-5 P0 fix: production login spawns the anti-idle ping task."""
    responder = _Responder()
    responder.on("POST", CAPITAL_SESSION_PATH, _login_handler(responder))
    responder.on("GET", CAPITAL_PING_PATH, lambda _r: httpx.Response(200, json={"status": "OK"}))
    client = httpx.AsyncClient(
        transport=_MockTransport(responder), base_url="https://demo-api"
    )
    sess = CapitalSession(api_key="K", identifier="e@x", password="pw", client=client)
    try:
        # Default auto_ping=True must schedule the loop after first login.
        assert sess._ping_task is None  # type: ignore[attr-defined]
        await sess.login()
        assert sess._ping_task is not None  # type: ignore[attr-defined]
        assert not sess._ping_task.done()  # type: ignore[union-attr,attr-defined]
        # Calling start_ping_loop again is idempotent.
        sess.start_ping_loop()
        assert sess._ping_task is not None  # type: ignore[attr-defined]
    finally:
        await sess.aclose()
        await client.aclose()
    # Verify request body shape.
    req = responder.calls[0]
    assert req.method == "POST"
    assert b"identifier" in req.content
    assert req.headers.get("X-CAP-API-KEY") == "K"


@pytest.mark.asyncio
async def test_anti_idle_9min_ping_extends_deadline() -> None:
    responder = _Responder()
    responder.on("POST", CAPITAL_SESSION_PATH, _login_handler(responder))
    responder.on("GET", CAPITAL_PING_PATH, lambda _r: httpx.Response(200, json={"status": "OK"}))
    client = httpx.AsyncClient(
        transport=_MockTransport(responder), base_url="https://demo-api"
    )
    sess = CapitalSession(
        api_key="K", identifier="e@x", password="pw", client=client, auto_ping=False
    )
    try:
        await sess.login()
        first_deadline = sess.tokens.deadline_ts  # type: ignore[union-attr]
        # Force a tiny gap to verify deadline moves forward.
        await asyncio.sleep(0.05)
        ok = await sess.ping()
        assert ok is True
        assert sess.tokens is not None
        assert sess.tokens.deadline_ts > first_deadline
    finally:
        await sess.aclose()
        await client.aclose()


@pytest.mark.asyncio
async def test_session_expire_auto_refresh() -> None:
    """A 401 response triggers re-login + retry once."""
    responder = _Responder()
    responder.on("POST", CAPITAL_SESSION_PATH, _login_handler(responder))
    state = {"first_call": True}

    def positions_handler(req: httpx.Request) -> httpx.Response:
        if state["first_call"]:
            state["first_call"] = False
            return httpx.Response(401, json={"errorCode": "expired"})
        return httpx.Response(200, json={"positions": []})

    responder.on("GET", CAPITAL_POSITIONS_PATH, positions_handler)
    client = httpx.AsyncClient(
        transport=_MockTransport(responder), base_url="https://demo-api"
    )
    sess = CapitalSession(
        api_key="K", identifier="e@x", password="pw", client=client, auto_ping=False
    )
    try:
        await sess.login()
        resp = await sess.request("GET", CAPITAL_POSITIONS_PATH)
        assert resp.status_code == 200
        # Two logins: initial + post-401 refresh.
        assert responder.login_count == 2
    finally:
        await sess.aclose()
        await client.aclose()


@pytest.mark.asyncio
async def test_session_token_expiry_triggers_relogin() -> None:
    responder = _Responder()
    responder.on("POST", CAPITAL_SESSION_PATH, _login_handler(responder))
    client = httpx.AsyncClient(
        transport=_MockTransport(responder), base_url="https://demo-api"
    )
    sess = CapitalSession(
        api_key="K", identifier="e@x", password="pw", client=client, auto_ping=False
    )
    try:
        toks = await sess.login()
        # Force expiry by rewinding deadline.
        sess._tokens = type(toks)(  # type: ignore[misc]
            cst=toks.cst,
            security_token=toks.security_token,
            minted_ts=toks.minted_ts,
            deadline_ts=time.time() - 1.0,
            account_id=toks.account_id,
        )
        toks2 = await sess.ensure_tokens()
        assert toks2.cst != toks.cst  # new token minted
        assert responder.login_count == 2
    finally:
        await sess.aclose()
        await client.aclose()


def test_token_deadline_is_540s() -> None:
    assert TOKEN_REFRESH_DEADLINE_SEC == 540.0


# ---------------------------------------------------------------------------
# Adapter (position lifecycle)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_position_open_close_lifecycle_mock() -> None:
    responder = _Responder()
    responder.on("POST", CAPITAL_SESSION_PATH, _login_handler(responder))
    responder.on(
        "POST",
        CAPITAL_POSITIONS_PATH,
        lambda _r: httpx.Response(
            200, json={"dealReference": "REF1", "dealStatus": "ACCEPTED"}
        ),
    )
    responder.on(
        "DELETE",
        f"{CAPITAL_POSITIONS_PATH}/DEAL1",
        lambda _r: httpx.Response(
            200, json={"dealReference": "REF2", "dealStatus": "ACCEPTED"}
        ),
    )

    client = httpx.AsyncClient(
        transport=_MockTransport(responder), base_url="https://demo-api"
    )
    sess = CapitalSession(
        api_key="K", identifier="e@x", password="pw", client=client, auto_ping=False
    )
    adapter = CapitalAdapter(sess)
    try:
        await sess.login()
        open_resp = await adapter.open_position(epic="EURUSD", direction="BUY", size=1.0)
        assert isinstance(open_resp, CapitalDealResponse)
        assert open_resp.ok is True
        assert open_resp.deal_reference == "REF1"
        close_resp = await adapter.close_position("DEAL1")
        assert close_resp.ok is True
    finally:
        await sess.aclose()
        await client.aclose()


@pytest.mark.asyncio
async def test_workingorder_endpoint_called() -> None:
    responder = _Responder()
    responder.on("POST", CAPITAL_SESSION_PATH, _login_handler(responder))
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = req.content
        return httpx.Response(200, json={"dealReference": "WO1", "dealStatus": "ACCEPTED"})

    responder.on("POST", CAPITAL_WORKINGORDERS_PATH, handler)
    client = httpx.AsyncClient(
        transport=_MockTransport(responder), base_url="https://demo-api"
    )
    sess = CapitalSession(
        api_key="K", identifier="e@x", password="pw", client=client, auto_ping=False
    )
    adapter = CapitalAdapter(sess)
    try:
        await sess.login()
        resp = await adapter.place_working_order(
            epic="EURUSD", direction="BUY", size=1.0, level=1.10
        )
    finally:
        await sess.aclose()
        await client.aclose()
    assert resp.ok
    assert b"LIMIT" in captured["body"]


@pytest.mark.asyncio
async def test_confirm_endpoint() -> None:
    responder = _Responder()
    responder.on("POST", CAPITAL_SESSION_PATH, _login_handler(responder))
    responder.on(
        "GET",
        f"{CAPITAL_CONFIRMS_PATH}/REF1",
        lambda _r: httpx.Response(
            200, json={"dealReference": "REF1", "dealStatus": "ACCEPTED", "level": 1.085}
        ),
    )
    client = httpx.AsyncClient(
        transport=_MockTransport(responder), base_url="https://demo-api"
    )
    sess = CapitalSession(
        api_key="K", identifier="e@x", password="pw", client=client, auto_ping=False
    )
    adapter = CapitalAdapter(sess)
    try:
        await sess.login()
        body = await adapter.confirm("REF1")
    finally:
        await sess.aclose()
        await client.aclose()
    assert body["dealStatus"] == "ACCEPTED"
    assert body["level"] == 1.085


# ---------------------------------------------------------------------------
# Constraint translator
# ---------------------------------------------------------------------------


def test_round_size_to_step() -> None:
    assert round_size_to_step(1.234, 0.01) == 1.23
    assert round_size_to_step(0.5, 0.1) == 0.5


def test_size_usd_to_lots_min_floor() -> None:
    c = CapitalMarketConstraint(
        epic="EURUSD",
        instrument_type="CURRENCIES",
        min_deal_size=1.0,
        step_size=0.1,
        decimal_places=5,
        leverage=30.0,
        pip_value_usd=10.0,
        base_ccy="EUR",
        quote_ccy="USD",
    )
    lots = size_usd_to_lots(constraint=c, notional_usd=0.5, last_price=1.10)
    # Bug C formula: unit = price × lotSize(1) = $1.10 → raw 0.4545 < min 1.0
    # → ceil-to-step(min) = 1.0 (venue floor expression, never a skip).
    assert lots == 1.0


def test_parse_capital_ts_treats_naive_as_utc() -> None:
    """Codex Day 5 P1 — naive ISO ts must be UTC, not host-local."""
    from polaris.venues.capital.adapter import _parse_capital_ts

    expected = 1_778_117_025  # 2026-05-07T01:23:45Z
    actual = _parse_capital_ts("2026-05-07T01:23:45")
    assert actual == expected
    assert _parse_capital_ts("2026-05-07T01:23:45Z") == expected


def test_size_usd_to_lots_above_min() -> None:
    c = CapitalMarketConstraint(
        epic="EURUSD",
        instrument_type="CURRENCIES",
        min_deal_size=1.0,
        step_size=0.1,
        decimal_places=5,
        leverage=30.0,
        pip_value_usd=10.0,
        base_ccy="EUR",
        quote_ccy="USD",
    )
    lots = size_usd_to_lots(constraint=c, notional_usd=2.75, last_price=1.10)
    # Bug C formula: unit = price × lotSize(1) = $1.10 → raw 2.5 → step 0.1 → 2.5
    # (floor-to-step never exceeds the T4 ask; leverage/pip never enter).
    assert lots == 2.5
