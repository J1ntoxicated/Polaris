"""Alpaca adapter — header auth, idempotent order retry, mocked REST.

Mirrors ``tests/test_okx_adapter.py``: a mock ``httpx.AsyncBaseTransport`` feeds
canned JSON; NO real network. Covers place_market_order happy + idempotent
retry (duplicate client_order_id → _lookup_existing_order returns the prior
order, no double-send), reject classification, account/positions parse, asset
constraint parse, clock/PDT data, and live-key refusal.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from polaris.venues.alpaca.adapter import (
    ALPACA_DATA_BASE,
    ALPACA_ORDERS_PATH,
    ALPACA_PAPER_BASE,
    AlpacaAdapter,
    AlpacaOrderResponse,
    resolve_alpaca_credentials,
)
from polaris.venues.alpaca.calendar import AlpacaClock, parse_account_pdt, parse_clock
from polaris.venues.alpaca.constraint_translator import (
    ALPACA_ASSETS_PATH,
    AssetConstraint,
    asset_row_to_constraint,
    fetch_assets,
)

# ---------------------------------------------------------------------------
# Mock transport (mirror test_okx_adapter._MockTransport)
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responder: Any) -> None:
        self.responder = responder
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        body = self.responder(request)
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)


def _adapter(client: httpx.AsyncClient) -> AlpacaAdapter:
    return AlpacaAdapter(api_key="K", secret="S", client=client)


# ---------------------------------------------------------------------------
# Credential resolution + live-key refusal (double safety)
# ---------------------------------------------------------------------------


def test_resolve_credentials_prefers_active_then_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "ACTIVE_K")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "ACTIVE_S")
    monkeypatch.setenv("ARCHIVE_ALPACA_PAPER_API_KEY", "ARC_K")
    monkeypatch.setenv("ARCHIVE_ALPACA_PAPER_SECRET", "ARC_S")
    key, secret = resolve_alpaca_credentials()
    assert key == "ACTIVE_K"
    assert secret == "ACTIVE_S"


def test_resolve_credentials_falls_back_to_archive_when_active_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "")
    monkeypatch.setenv("ARCHIVE_ALPACA_PAPER_API_KEY", "ARC_K")
    monkeypatch.setenv("ARCHIVE_ALPACA_PAPER_SECRET", "ARC_S")
    key, secret = resolve_alpaca_credentials()
    assert key == "ARC_K"
    assert secret == "ARC_S"


def test_resolve_credentials_archive_only_active_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Production-exact: ``.env`` carries ONLY ``ARCHIVE_ALPACA_PAPER_*`` (the
    # active ``ALPACA_PAPER_*`` vars are absent entirely, not just empty). The
    # WS gate must still resolve creds so the Alpaca quote WS spawns — this is
    # why the fresh DB eventually gets alpaca quote_ticks (the resolution is
    # robust; 0 ticks at restart is a WS-warmup timing artifact, not a gate bug).
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_SECRET", raising=False)
    monkeypatch.setenv("ARCHIVE_ALPACA_PAPER_API_KEY", "ARC_K")
    monkeypatch.setenv("ARCHIVE_ALPACA_PAPER_SECRET", "ARC_S")
    key, secret = resolve_alpaca_credentials()
    assert key == "ARC_K"
    assert secret == "ARC_S"


def test_adapter_refuses_live_base_url() -> None:
    """DEMO/PAPER ONLY — a live trade base must be refused (double safety)."""
    with pytest.raises(ValueError):
        AlpacaAdapter(
            api_key="K", secret="S", base_url="https://api.alpaca.markets"
        )


def test_adapter_refuses_blank_creds() -> None:
    with pytest.raises(ValueError):
        AlpacaAdapter(api_key="", secret="S")


def test_adapter_defaults_to_paper_bases() -> None:
    a = AlpacaAdapter(api_key="K", secret="S")
    assert a.base_url == ALPACA_PAPER_BASE
    assert a.data_base_url == ALPACA_DATA_BASE


# ---------------------------------------------------------------------------
# place_market_order — happy path (notional, header auth, never logs secret)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_market_order_happy_notional() -> None:
    captured: dict[str, Any] = {}

    def responder(req: httpx.Request) -> Any:
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "POST":
            captured["headers"] = dict(req.headers)
            sent = json.loads(req.content.decode())
            captured["body"] = sent
            assert sent["symbol"] == "AAPL"
            assert sent["side"] == "buy"
            assert sent["type"] == "market"
            # notional=USD avoids share rounding.
            assert float(sent["notional"]) == pytest.approx(250.0)
            assert "qty" not in sent
            return {
                "id": "ord-1",
                "client_order_id": sent["client_order_id"],
                "status": "accepted",
                "symbol": "AAPL",
                "side": "buy",
                "filled_qty": "0",
                "filled_avg_price": None,
            }
        return httpx.Response(404, json={"message": "not found"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        resp = await adapter.place_market_order(
            symbol="AAPL",
            side="buy",
            notional_usd=250.0,
            client_order_id="polaris-equity-001",
        )
    finally:
        await client.aclose()
    assert isinstance(resp, AlpacaOrderResponse)
    assert resp.ok is True
    assert resp.venue_order_id == "ord-1"
    # Header auth present, secret value never appears in a header key.
    h = captured["headers"]
    assert h.get("apca-api-key-id") == "K"
    assert h.get("apca-api-secret-key") == "S"


@pytest.mark.asyncio
async def test_place_market_order_reject_classification() -> None:
    """A 422 business reject is parsed to ok=False with code/msg, not raised."""

    def responder(req: httpx.Request) -> Any:
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "POST":
            return httpx.Response(
                422,
                json={"code": 40310000, "message": "insufficient buying power"},
            )
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "GET":
            return httpx.Response(200, json=[])  # idempotency lookup: none
        return httpx.Response(404, json={"message": "not found"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        resp = await adapter.place_market_order(
            symbol="AAPL",
            side="buy",
            notional_usd=1e9,
            client_order_id="polaris-equity-002",
        )
    finally:
        await client.aclose()
    assert resp.ok is False
    # H2: Alpaca's numeric 40310000 (insufficient buying power) is normalized to
    # the SSOT semantic token so _is_external_reject matches it as non-fault.
    assert resp.code == "insufficient_buying_power"
    assert "buying power" in resp.msg
    # The raw numeric code is preserved in ``raw`` for audit/debugging.
    assert resp.raw.get("code") == 40310000


# ---------------------------------------------------------------------------
# H2 — reject-code normalization: numeric Alpaca codes / HTTP statuses are
# mapped to the SSOT semantic vocabulary (C_alpaca_equity.external_reject_codes)
# so a genuine external reject classifies non-fault. A validation 422 normalizes
# to a token OUTSIDE that set so it still faults (anomalous → halt path).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "raw_code", "message", "expected_token"),
    [
        # 403 + 40310000 family → buying power / restriction / not-authorized.
        (403, 40310000, "insufficient buying power", "insufficient_buying_power"),
        (403, 40310000, "account is not authorized to trade", "insufficient_buying_power"),
        # 403 + 40310100 → pattern-day-trading protection.
        (403, 40310100, "trade denied due to pattern day trading protection", "pdt_block"),
        # Generic 403 with no recognized numeric code → forbidden (auth/perm).
        (403, None, "forbidden", "forbidden"),
        (401, None, "unauthorized", "forbidden"),
        # Market closed (Alpaca surfaces this on the 403 family) → market_closed.
        (403, None, "market is closed", "market_closed"),
    ],
)
@pytest.mark.asyncio
async def test_reject_code_normalized_to_external_token(
    status: int, raw_code: int | None, message: str, expected_token: str
) -> None:
    """Each genuine external Alpaca reject normalizes to an SSOT external token."""

    def responder(req: httpx.Request) -> Any:
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "POST":
            body: dict[str, Any] = {"message": message}
            if raw_code is not None:
                body["code"] = raw_code
            return httpx.Response(status, json=body)
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        resp = await adapter.place_market_order(
            symbol="AAPL", side="buy", notional_usd=1e9,
            client_order_id=f"polaris-ext-{expected_token}",
        )
    finally:
        await client.aclose()
    assert resp.ok is False
    assert resp.code == expected_token


@pytest.mark.asyncio
async def test_reject_validation_422_normalizes_to_fault_token() -> None:
    """A 422 validation reject (invalid order params — a client/strategy bug) must
    NOT normalize to an external token; it stays anomalous so it still faults."""
    from polaris.core.streams import resolve_stream

    def responder(req: httpx.Request) -> Any:
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "POST":
            return httpx.Response(
                422, json={"code": 42210000, "message": "invalid order type"}
            )
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        resp = await adapter.place_market_order(
            symbol="ZZZ", side="buy", notional_usd=100.0,
            client_order_id="polaris-422-fault",
        )
    finally:
        await client.aclose()
    assert resp.ok is False
    # The normalized token must NOT be in the stream's external set → it faults.
    external = resolve_stream("alpaca").external_reject_codes
    assert resp.code not in external


# ---------------------------------------------------------------------------
# Idempotency — duplicate client_order_id → lookup returns prior, no double-send
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    import polaris.venues.alpaca.adapter as alpaca_adapter

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(alpaca_adapter, "_async_sleep", _no_sleep)


@pytest.mark.asyncio
async def test_order_5xx_then_success_retries() -> None:
    """A single 5xx on order POST is absorbed; the retry succeeds (no error)."""
    state = {"post": 0}

    def responder(req: httpx.Request) -> Any:
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "POST":
            state["post"] += 1
            if state["post"] == 1:
                return httpx.Response(503, json={"message": "service unavailable"})
            sent = json.loads(req.content.decode())
            return {
                "id": "ord-9",
                "client_order_id": sent["client_order_id"],
                "status": "accepted",
                "symbol": "AAPL",
            }
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "GET":
            return httpx.Response(200, json=[])  # not found between retries
        return httpx.Response(404, json={"message": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        resp = await adapter.place_market_order(
            symbol="AAPL",
            side="buy",
            notional_usd=100.0,
            client_order_id="polaris-equity-retry",
        )
    finally:
        await client.aclose()
    assert resp.ok is True
    assert resp.venue_order_id == "ord-9"
    assert state["post"] == 2  # 1 failure + 1 retry


@pytest.mark.asyncio
async def test_order_idempotency_landed_order_not_duplicated() -> None:
    """If the failed POST actually landed, the client_order_id lookup returns it —
    the retry must NOT submit a second order (no duplicate)."""

    class _IdempotentTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.order_post_count = 0
            self.lookup_count = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == ALPACA_ORDERS_PATH and request.method == "POST":
                self.order_post_count += 1
                # POST times out AFTER the server accepted the order.
                raise httpx.ReadTimeout("response lost")
            if path == ALPACA_ORDERS_PATH and request.method == "GET":
                self.lookup_count += 1
                # client_order_id lookup shows the order DID land.
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": "ord-555",
                            "client_order_id": "polaris-equity-idem",
                            "status": "accepted",
                            "symbol": "AAPL",
                        }
                    ],
                )
            return httpx.Response(404, json={"message": "nf"})

    transport = _IdempotentTransport()
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        resp = await adapter.place_market_order(
            symbol="AAPL",
            side="buy",
            notional_usd=100.0,
            client_order_id="polaris-equity-idem",
        )
    finally:
        await client.aclose()
    # Exactly ONE POST — the idempotency lookup found the landed order.
    assert transport.order_post_count == 1
    assert transport.lookup_count >= 1
    assert resp.ok is True
    assert resp.venue_order_id == "ord-555"


@pytest.mark.asyncio
async def test_order_4xx_does_not_retry() -> None:
    """A 4xx business reject is parsed (not retried) — single POST."""
    state = {"post": 0}

    def responder(req: httpx.Request) -> Any:
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "POST":
            state["post"] += 1
            return httpx.Response(
                422, json={"code": 42210000, "message": "invalid symbol"}
            )
        if req.url.path == ALPACA_ORDERS_PATH and req.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        resp = await adapter.place_market_order(
            symbol="ZZZ",
            side="buy",
            notional_usd=100.0,
            client_order_id="polaris-equity-4xx",
        )
    finally:
        await client.aclose()
    assert resp.ok is False
    assert state["post"] == 1  # no retry on a business 4xx


# ---------------------------------------------------------------------------
# Account / positions parse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_account_parse() -> None:
    def responder(req: httpx.Request) -> Any:
        if req.url.path == "/v2/account":
            return {
                "status": "ACTIVE",
                "buying_power": "318843.2",
                "cash": "79710.8",
                "equity": "79886.41",
                "daytrade_count": 0,
                "pattern_day_trader": True,
            }
        return httpx.Response(404, json={"message": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        acct = await adapter.fetch_account()
    finally:
        await client.aclose()
    assert acct["status"] == "ACTIVE"
    assert float(acct["buying_power"]) == pytest.approx(318843.2)


@pytest.mark.asyncio
async def test_fetch_positions_parse() -> None:
    def responder(req: httpx.Request) -> Any:
        if req.url.path == "/v2/positions":
            return [
                {
                    "symbol": "AAPL",
                    "qty": "3",
                    "side": "long",
                    "avg_entry_price": "190.0",
                    "market_value": "600.0",
                }
            ]
        return httpx.Response(404, json={"message": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        positions = await adapter.fetch_positions()
    finally:
        await client.aclose()
    assert isinstance(positions, list)
    assert positions[0]["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# Bars / quote (data endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_quote_uses_data_base() -> None:
    captured: dict[str, Any] = {}

    def responder(req: httpx.Request) -> Any:
        captured["host"] = req.url.host
        return {"quote": {"ap": 191.0, "bp": 190.5, "as": 1, "bs": 2}}

    transport = _MockTransport(responder)
    # The data client is injected separately so the host is observable.
    data_client = httpx.AsyncClient(transport=transport, base_url=ALPACA_DATA_BASE)
    adapter = AlpacaAdapter(api_key="K", secret="S", data_client=data_client)
    try:
        quote = await adapter.fetch_quote("AAPL")
    finally:
        await data_client.aclose()
    assert quote["ap"] == 191.0
    assert "data" in captured["host"]


# ---------------------------------------------------------------------------
# Constraint translator (assets)
# ---------------------------------------------------------------------------


def test_asset_row_to_constraint_defaults() -> None:
    c = asset_row_to_constraint(
        {
            "symbol": "AAPL",
            "class": "us_equity",
            "tradable": True,
            "fractionable": True,
            "shortable": True,
            "easy_to_borrow": True,
            "status": "active",
        }
    )
    assert c is not None
    assert c.symbol == "AAPL"
    assert c.tradable is True
    assert c.fractionable is True
    assert c.shortable is True
    assert c.easy_to_borrow is True
    # Fractionable → min_order_size small; price_increment default penny.
    assert c.min_order_size > 0.0
    assert c.price_increment > 0.0


@pytest.mark.asyncio
async def test_fetch_assets_caches_by_symbol() -> None:
    def responder(req: httpx.Request) -> Any:
        if req.url.path == ALPACA_ASSETS_PATH:
            return [
                {
                    "symbol": "AAPL",
                    "class": "us_equity",
                    "tradable": True,
                    "fractionable": True,
                    "shortable": True,
                    "easy_to_borrow": True,
                    "status": "active",
                },
                {"symbol": "", "class": "us_equity"},  # invalid → skipped
            ]
        return httpx.Response(404, json={"message": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        out = await fetch_assets(adapter)
    finally:
        await client.aclose()
    assert "AAPL" in out
    assert len(out) == 1
    assert isinstance(out["AAPL"], AssetConstraint)


# ---------------------------------------------------------------------------
# Calendar / clock + PDT data provider (T13 gate consumes this; T13 != T9)
# ---------------------------------------------------------------------------


def test_parse_clock() -> None:
    clock = parse_clock(
        {
            "is_open": True,
            "next_open": "2026-06-01T09:30:00-04:00",
            "next_close": "2026-05-30T16:00:00-04:00",
            "timestamp": "2026-05-30T12:00:00-04:00",
        }
    )
    assert isinstance(clock, AlpacaClock)
    assert clock.is_open is True
    assert clock.next_open == "2026-06-01T09:30:00-04:00"


def test_parse_account_pdt() -> None:
    dt_count, is_pdt = parse_account_pdt(
        {"daytrade_count": 2, "pattern_day_trader": True}
    )
    assert dt_count == 2
    assert is_pdt is True


@pytest.mark.asyncio
async def test_fetch_clock_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"calls": 0}

    def responder(req: httpx.Request) -> Any:
        if req.url.path == "/v2/clock":
            state["calls"] += 1
            return {"is_open": True, "next_open": "x", "next_close": "y", "timestamp": "z"}
        return httpx.Response(404, json={"message": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url=ALPACA_PAPER_BASE)
    adapter = _adapter(client)
    try:
        c1 = await adapter.fetch_clock()
        c2 = await adapter.fetch_clock()  # within TTL → cached, no 2nd HTTP
    finally:
        await client.aclose()
    assert c1.is_open is True
    assert c2.is_open is True
    assert state["calls"] == 1
