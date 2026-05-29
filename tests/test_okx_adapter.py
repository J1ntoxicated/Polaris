"""OKX adapter — signing, clOrdId sanitization, signed REST mocks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest

from polaris.venues.okx.adapter import (
    DEFAULT_SLIPPAGE_BPS,
    OKX_PLACE_ORDER_PATH,
    OKXAdapter,
    OKXOrderResponse,
    sanitize_clordid,
)
from polaris.venues.okx.constraint_translator import (
    InstrumentConstraint,
    fetch_instruments,
    round_down_to_step,
    round_price_to_tick,
    validate_min_notional,
)
from polaris.venues.okx.signing import (
    OKX_DEMO_SIM_HEADER,
    build_signed_headers,
    iso_timestamp,
    okx_signature,
)

# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def test_signing_hmac_correct() -> None:
    """OK-ACCESS-SIGN must be base64(HMAC_SHA256(secret, ts+method+path+body))."""
    ts = "2026-05-07T01:23:45.678Z"
    method = "POST"
    path = "/api/v5/trade/order"
    body = json.dumps({"instId": "BTC-USDT", "tdMode": "cash"}, separators=(",", ":"))
    secret = "supersecret"
    expected = base64.b64encode(
        hmac.new(
            secret.encode("utf-8"),
            f"{ts}{method}{path}{body}".encode(),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")
    actual = okx_signature(
        secret=secret, timestamp=ts, method=method, request_path=path, body=body
    )
    assert actual == expected


def test_signing_uppercases_method() -> None:
    ts = "2026-05-07T01:23:45.678Z"
    a = okx_signature(secret="s", timestamp=ts, method="get", request_path="/x")
    b = okx_signature(secret="s", timestamp=ts, method="GET", request_path="/x")
    assert a == b


def test_iso_timestamp_format() -> None:
    out = iso_timestamp(now_ms=1_762_476_225_678)
    # ms precision + 'Z' suffix.
    assert out.endswith("Z")
    assert "." in out
    head, tail = out.rsplit(".", 1)
    assert len(tail) == len("000Z")  # "678Z"


def test_build_signed_headers_demo_includes_sim_flag() -> None:
    headers = build_signed_headers(
        api_key="K",
        secret="S",
        passphrase="P",
        method="GET",
        request_path="/api/v5/account/balance",
        demo=True,
    )
    assert headers[OKX_DEMO_SIM_HEADER] == "1"
    for h in ("OK-ACCESS-KEY", "OK-ACCESS-SIGN", "OK-ACCESS-TIMESTAMP", "OK-ACCESS-PASSPHRASE"):
        assert h in headers


def test_build_signed_headers_live_omits_sim_flag() -> None:
    headers = build_signed_headers(
        api_key="K", secret="S", passphrase="P", method="GET", request_path="/x", demo=False
    )
    assert OKX_DEMO_SIM_HEADER not in headers


def test_build_signed_headers_rejects_blank_creds() -> None:
    with pytest.raises(ValueError):
        build_signed_headers(
            api_key="", secret="S", passphrase="P", method="GET", request_path="/x"
        )


# ---------------------------------------------------------------------------
# clOrdId sanitization
# ---------------------------------------------------------------------------


def test_clorderid_alphanumeric_no_hyphen() -> None:
    out = sanitize_clordid("polaris-vb-2026_05_07-001")
    assert "-" not in out
    assert "_" not in out
    assert all(c.isalnum() for c in out)


def test_clorderid_starts_with_letter() -> None:
    out = sanitize_clordid("123-numeric-prefix")
    assert out[0].isalpha()


def test_clorderid_max_32_chars() -> None:
    out = sanitize_clordid("a" * 100)
    assert len(out) == 32


def test_clorderid_empty_input_safe() -> None:
    out = sanitize_clordid("---___")
    assert out  # produces at least 'p'
    assert out[0].isalpha()


# ---------------------------------------------------------------------------
# Constraint helpers
# ---------------------------------------------------------------------------


def test_round_down_to_step_decimal_safe() -> None:
    # float drift would give 0.30000000000000004 here.
    assert round_down_to_step(0.3 + 0.01, 0.01) == 0.31
    assert round_down_to_step(0.123456789, 0.0001) == 0.1234


def test_round_price_to_tick() -> None:
    assert round_price_to_tick(67_321.45, 0.1) == 67_321.4
    assert round_price_to_tick(0.0, 0.1) == 0.0


def test_validate_min_notional_pass_fail() -> None:
    c = InstrumentConstraint(
        inst_id="BTC-USDT",
        base_ccy="BTC",
        quote_ccy="USDT",
        lot_sz=1e-8,
        min_sz=0.0001,
        tick_sz=0.1,
        state="live",
    )
    ok, _ = validate_min_notional(constraint=c, notional_usd=10.0, last_price=60_000.0)
    assert ok
    bad, reason = validate_min_notional(
        constraint=c, notional_usd=1.0, last_price=60_000.0
    )
    assert not bad
    assert "min_sz" in reason


# ---------------------------------------------------------------------------
# Adapter (mocked HTTP)
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


@pytest.mark.asyncio
async def test_market_buy_response_parse_mock() -> None:
    """``place_market_order`` parses ``data[0]`` for ordId/clOrdId."""

    def responder(req: httpx.Request) -> Any:
        if req.url.path == "/api/v5/market/ticker":
            return {"code": "0", "data": [{"bidPx": "60000", "askPx": "60010"}]}
        if req.url.path == OKX_PLACE_ORDER_PATH:
            assert req.method == "POST"
            sent = json.loads(req.content.decode())
            assert sent["instId"] == "BTC-USDT"
            assert sent["tdMode"] == "cash"
            # IOC sends sz in BASE ccy (no tgtCcy) + px clamp from ref price.
            assert sent["ordType"] == "ioc"
            assert "px" in sent
            assert "tgtCcy" not in sent
            # 10 USDT notional / ~60003 px ≈ 0.000167 BTC.
            assert float(sent["sz"]) < 0.001
            assert "-" not in sent["clOrdId"]
            return {
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "ordId": "999",
                        "clOrdId": sent["clOrdId"],
                        "tag": "",
                        "sCode": "0",
                        "sMsg": "",
                    }
                ],
            }
        return httpx.Response(404, json={"code": "1", "msg": "not found"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.place_market_order(
            inst_id="BTC-USDT",
            side="buy",
            notional_usd=10.0,
            client_order_id="polaris-vb-2026_05_07-001",
            slippage_bps=DEFAULT_SLIPPAGE_BPS,
        )
    finally:
        await client.aclose()
    assert isinstance(resp, OKXOrderResponse)
    assert resp.ok is True
    assert resp.venue_order_id == "999"
    assert resp.client_order_id is not None and "-" not in resp.client_order_id


@pytest.mark.asyncio
async def test_market_buy_uses_quote_ccy_for_market_ord_type() -> None:
    """When ordType=market, sz is in USDT and tgtCcy=quote_ccy is set."""

    def responder(req: httpx.Request) -> Any:
        if req.url.path == OKX_PLACE_ORDER_PATH:
            sent = json.loads(req.content.decode())
            assert sent["ordType"] == "market"
            assert sent["tgtCcy"] == "quote_ccy"
            # USD notional path → sz == 10.
            assert float(sent["sz"]) == pytest.approx(10.0)
            return {
                "code": "0",
                "data": [{"ordId": "1", "clOrdId": sent["clOrdId"], "sCode": "0"}],
            }
        return httpx.Response(404, json={"code": "1"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.place_market_order(
            inst_id="BTC-USDT",
            side="buy",
            notional_usd=10.0,
            client_order_id="polarisvb",
            ord_type="market",
        )
    finally:
        await client.aclose()
    assert resp.ok


@pytest.mark.asyncio
async def test_market_order_51201_parsed_as_reject() -> None:
    """A 51201 (1000-USDT market cap) response parses to a non-ok reject with
    code=51201 — the venue rule FIX 1 avoids by splitting, locked here so the
    classifier (external, non-fault) keys off the right code."""

    def responder(req: httpx.Request) -> Any:
        if req.url.path == OKX_PLACE_ORDER_PATH:
            return {
                "code": "1",
                "msg": "",
                "data": [
                    {
                        "ordId": "",
                        "clOrdId": "polarisvb",
                        "sCode": "51201",
                        "sMsg": "The value of a market order can't exceed 1000USDT.",
                    }
                ],
            }
        return httpx.Response(404, json={"code": "1"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.place_market_order(
            inst_id="ALGO-USDT", side="buy", notional_usd=1_468.0,
            client_order_id="polarisvb", ord_type="market",
        )
    finally:
        await client.aclose()
    assert resp.ok is False
    assert resp.code == "51201"
    assert resp.venue_order_id is None


@pytest.mark.asyncio
async def test_position_fetch_mock() -> None:
    def responder(_req: httpx.Request) -> Any:
        return {
            "code": "0",
            "data": [{"instId": "BTC-USDT", "pos": "0.001", "ccy": "BTC"}],
        }

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        body = await adapter.fetch_positions()
    finally:
        await client.aclose()
    assert body["code"] == "0"
    assert body["data"][0]["instId"] == "BTC-USDT"


@pytest.mark.asyncio
async def test_fill_query_mock() -> None:
    def responder(_req: httpx.Request) -> Any:
        return {
            "code": "0",
            "data": [
                {
                    "ordId": "999",
                    "clOrdId": "polarisvb",
                    "instId": "BTC-USDT",
                    "tdMode": "cash",
                    "side": "buy",
                    "ordType": "ioc",
                    "sz": "10",
                    "tgtCcy": "quote_ccy",
                    "avgPx": "60005",
                    "accFillSz": "10",
                    "fee": "-0.0035",
                    "feeCcy": "USDT",
                    "state": "filled",
                    "uTime": "1762476225678",
                }
            ],
        }

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        body = await adapter.fetch_order(inst_id="BTC-USDT", ord_id="999")
    finally:
        await client.aclose()
    assert body["data"][0]["state"] == "filled"


@pytest.mark.asyncio
async def test_fetch_instruments_filters_invalid_rows() -> None:
    def responder(_req: httpx.Request) -> Any:
        return {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT",
                    "baseCcy": "BTC",
                    "quoteCcy": "USDT",
                    "lotSz": "0.00000001",
                    "minSz": "0.0001",
                    "tickSz": "0.1",
                    "state": "live",
                },
                {"instId": "", "baseCcy": "", "quoteCcy": ""},  # invalid
            ],
        }

    transport = _MockTransport(responder)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://us.okx.com"
    ) as client:
        out = await fetch_instruments(base_url="https://us.okx.com", client=client)
    assert "BTC-USDT" in out
    assert len(out) == 1


# ---------------------------------------------------------------------------
# #12 Order placement 5xx / timeout retry (exponential backoff, idempotent)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero out the retry sleep so retry tests run instantly."""
    import polaris.venues.okx.adapter as okx_adapter

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(okx_adapter, "_async_sleep", _no_sleep)


class _SequenceTransport(httpx.AsyncBaseTransport):
    """Returns queued responses/exceptions in order; tracks order-POST count."""

    def __init__(self, order_responses: list[Any], *, ticker: dict[str, Any] | None = None) -> None:
        self._order_responses = list(order_responses)
        self._ticker = ticker or {"bidPx": "60000", "askPx": "60010"}
        self.order_post_count = 0
        self.fetch_order_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v5/market/ticker":
            return httpx.Response(200, json={"code": "0", "data": [self._ticker]})
        if path == OKX_PLACE_ORDER_PATH and request.method == "POST":
            self.order_post_count += 1
            nxt = self._order_responses.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        if path == "/api/v5/trade/order" and request.method == "GET":
            # Idempotency lookup by clOrdId — default: order NOT found (empty).
            self.fetch_order_count += 1
            return httpx.Response(200, json={"code": "0", "data": []})
        return httpx.Response(404, json={"code": "1", "msg": "not found"})


def _ok_order(cl: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "code": "0",
            "msg": "",
            "data": [{"ordId": "777", "clOrdId": cl, "sCode": "0", "sMsg": ""}],
        },
    )


@pytest.mark.asyncio
async def test_order_5xx_then_success_retries() -> None:
    """A single 5xx on order POST is absorbed; a retry succeeds (no error)."""
    transport = _SequenceTransport(
        order_responses=[
            httpx.Response(503, json={"code": "1", "msg": "service unavailable"}),
            _ok_order("polarisvb"),
        ]
    )
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.place_market_order(
            inst_id="BTC-USDT",
            side="buy",
            notional_usd=10.0,
            client_order_id="polarisvb",
            ord_type="market",
        )
    finally:
        await client.aclose()
    assert resp.ok is True
    assert resp.venue_order_id == "777"
    assert transport.order_post_count == 2  # 1 failure + 1 retry


@pytest.mark.asyncio
async def test_order_timeout_then_success_retries() -> None:
    """A network timeout on order POST is absorbed; the retry succeeds."""
    transport = _SequenceTransport(
        order_responses=[
            httpx.ConnectTimeout("timed out"),
            _ok_order("polarisvb"),
        ]
    )
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.place_market_order(
            inst_id="BTC-USDT",
            side="buy",
            notional_usd=10.0,
            client_order_id="polarisvb",
            ord_type="market",
        )
    finally:
        await client.aclose()
    assert resp.ok is True
    assert transport.order_post_count == 2


@pytest.mark.asyncio
async def test_order_persistent_5xx_propagates_error() -> None:
    """Continuous 5xx (initial + all retries) ultimately raises — no silent OK."""
    transport = _SequenceTransport(
        order_responses=[
            httpx.Response(502, json={"code": "1", "msg": "bad gateway"}),
            httpx.Response(502, json={"code": "1", "msg": "bad gateway"}),
            httpx.Response(502, json={"code": "1", "msg": "bad gateway"}),
        ]
    )
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.place_market_order(
                inst_id="BTC-USDT",
                side="buy",
                notional_usd=10.0,
                client_order_id="polarisvb",
                ord_type="market",
            )
    finally:
        await client.aclose()
    # initial + 2 retries (ORDER_RETRY_MAX) = 3 attempts.
    assert transport.order_post_count == 3


@pytest.mark.asyncio
async def test_order_4xx_does_not_retry() -> None:
    """A 4xx (client error, e.g. bad param) is NOT retried — fail fast."""
    transport = _SequenceTransport(
        order_responses=[httpx.Response(400, json={"code": "51000", "msg": "param error"})]
    )
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.place_market_order(
                inst_id="BTC-USDT",
                side="buy",
                notional_usd=10.0,
                client_order_id="polarisvb",
                ord_type="market",
            )
    finally:
        await client.aclose()
    assert transport.order_post_count == 1  # no retry on 4xx


@pytest.mark.asyncio
async def test_order_idempotency_landed_order_not_duplicated() -> None:
    """If the failed POST actually landed, the clOrdId lookup returns it —
    the retry must NOT submit a second order (no duplicate)."""

    class _IdempotentTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.order_post_count = 0
            self.fetch_order_count = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == OKX_PLACE_ORDER_PATH and request.method == "POST":
                self.order_post_count += 1
                # First POST times out AFTER the server accepted the order.
                raise httpx.ReadTimeout("response lost")
            if path == "/api/v5/trade/order" and request.method == "GET":
                self.fetch_order_count += 1
                # clOrdId lookup shows the order DID land (live/filled).
                return httpx.Response(
                    200,
                    json={
                        "code": "0",
                        "data": [
                            {
                                "ordId": "555",
                                "clOrdId": "polarisvb",
                                "state": "live",
                                "sCode": "0",
                            }
                        ],
                    },
                )
            return httpx.Response(404, json={"code": "1"})

    transport = _IdempotentTransport()
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.place_market_order(
            inst_id="BTC-USDT",
            side="buy",
            notional_usd=10.0,
            client_order_id="polarisvb",
            ord_type="market",
        )
    finally:
        await client.aclose()
    # Exactly ONE POST — the idempotency lookup found the landed order, so no
    # second submission. Response reflects the existing venue order.
    assert transport.order_post_count == 1
    assert transport.fetch_order_count >= 1
    assert resp.ok is True
    assert resp.venue_order_id == "555"


@pytest.mark.asyncio
async def test_signed_request_includes_auth_headers() -> None:
    captured: dict[str, dict[str, str]] = {}

    def responder(req: httpx.Request) -> Any:
        captured["headers"] = dict(req.headers)
        return {"code": "0", "data": []}

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        await adapter.fetch_balance("USDT")
    finally:
        await client.aclose()
    h = captured["headers"]
    assert "ok-access-key" in h
    assert "ok-access-sign" in h
    assert "ok-access-timestamp" in h
    assert "ok-access-passphrase" in h
    assert h.get("x-simulated-trading") == "1"
