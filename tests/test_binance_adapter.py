"""Binance USDⓈ-M futures testnet adapter — signing, klines, signed REST mocks."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from polaris.venues.binance.adapter import (
    BINANCE_CANCEL_ORDER_PATH,
    BINANCE_PLACE_ORDER_PATH,
    BinanceFuturesAdapter,
    BinanceOrderResponse,
)
from polaris.venues.binance.constraint_translator import (
    FuturesConstraint,
    fetch_exchange_info,
    round_down_to_step,
    round_price_to_tick,
    translate_notional_to_qty,
    validate_min_notional,
)
from polaris.venues.binance.market_data import (
    binance_kline_to_bar,
    fetch_binance_bars,
)
from polaris.venues.binance.signing import (
    BINANCE_APIKEY_HEADER,
    binance_signature,
    build_signed_query,
)

# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def test_signature_hmac_correct() -> None:
    """signature must be hex(HMAC_SHA256(secret, query_string))."""
    secret = "supersecret"
    qs = "symbol=BTCUSDT&side=BUY&type=MARKET&quantity=0.001&timestamp=1762476225678"
    expected = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    assert binance_signature(secret=secret, query_string=qs) == expected


def test_signature_deterministic_known_vector() -> None:
    """Binance's documented worked example (HMAC SHA256, hex)."""
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0"
    qs = (
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC"
        "&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    )
    expected = "b89008e7051ffbf2242be7dc5ae67fd146e6430688627b802c0cbec146e46aef"
    assert binance_signature(secret=secret, query_string=qs) == expected


def test_build_signed_query_appends_ts_recvwindow_and_signature() -> None:
    secret = "S"
    out = build_signed_query(
        secret=secret,
        params={"symbol": "BTCUSDT", "side": "BUY"},
        recv_window=5000,
        timestamp_ms=1762476225678,
    )
    parsed = parse_qs(out)
    assert parsed["symbol"] == ["BTCUSDT"]
    assert parsed["timestamp"] == ["1762476225678"]
    assert parsed["recvWindow"] == ["5000"]
    assert "signature" in parsed
    # Signature signs everything BEFORE the &signature= suffix.
    payload = out.rsplit("&signature=", 1)[0]
    expected_sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    assert parsed["signature"] == [expected_sig]


def test_build_signed_query_deterministic_for_fixed_ts() -> None:
    a = build_signed_query(secret="S", params={"symbol": "BTCUSDT"}, timestamp_ms=1)
    b = build_signed_query(secret="S", params={"symbol": "BTCUSDT"}, timestamp_ms=1)
    assert a == b


def test_signature_rejects_blank_secret() -> None:
    with pytest.raises(ValueError):
        binance_signature(secret="", query_string="x=1")
    with pytest.raises(ValueError):
        build_signed_query(secret="", params={"a": 1})


# ---------------------------------------------------------------------------
# Constraint helpers / exchangeInfo parsing
# ---------------------------------------------------------------------------


def _btc_constraint() -> FuturesConstraint:
    return FuturesConstraint(
        symbol="BTCUSDT",
        base_ccy="BTC",
        quote_ccy="USDT",
        step_size=0.001,
        min_qty=0.001,
        tick_size=0.1,
        min_notional=5.0,
        status="TRADING",
    )


def test_round_down_to_step_decimal_safe() -> None:
    assert round_down_to_step(0.3 + 0.01, 0.01) == 0.31
    assert round_down_to_step(0.123456, 0.001) == 0.123


def test_round_price_to_tick() -> None:
    assert round_price_to_tick(67_321.45, 0.1) == 67_321.4
    assert round_price_to_tick(0.0, 0.1) == 0.0


def test_translate_notional_to_qty_floors_step() -> None:
    c = _btc_constraint()
    # 100 USD / 60000 = 0.001666... → floor to 0.001 step → 0.001.
    qty = translate_notional_to_qty(constraint=c, notional_usd=100.0, price=60_000.0)
    assert qty == 0.001
    assert translate_notional_to_qty(constraint=c, notional_usd=100.0, price=0.0) == 0.0


def test_validate_min_notional_pass_fail() -> None:
    c = _btc_constraint()
    ok, _ = validate_min_notional(constraint=c, qty=0.001, price=60_000.0)
    assert ok
    bad_qty, reason = validate_min_notional(constraint=c, qty=0.0001, price=60_000.0)
    assert not bad_qty
    assert "min_qty" in reason
    bad_notional, reason2 = validate_min_notional(constraint=c, qty=0.001, price=1.0)
    assert not bad_notional
    assert "min_notional" in reason2


@pytest.mark.asyncio
async def test_fetch_exchange_info_parses_filters() -> None:
    def responder(_req: httpx.Request) -> Any:
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                },
                {"symbol": "", "filters": []},  # invalid → dropped
            ]
        }

    transport = _MockTransport(responder)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://testnet.binancefuture.com"
    ) as client:
        out = await fetch_exchange_info(
            base_url="https://testnet.binancefuture.com", client=client
        )
    assert "BTCUSDT" in out and len(out) == 1
    c = out["BTCUSDT"]
    assert c.step_size == 0.001
    assert c.tick_size == 0.1
    assert c.min_notional == 5.0


# ---------------------------------------------------------------------------
# Klines → Bar
# ---------------------------------------------------------------------------


def test_kline_to_bar_maps_fields() -> None:
    row = [
        1762476180000,  # openTime ms
        "60000.0",
        "60100.0",
        "59950.0",
        "60050.0",
        "12.5",  # volume
        1762476239999,  # closeTime
        "750000.0",  # quote volume
        42,  # trades
        "6.0",
        "360000.0",
        "0",
    ]
    bar = binance_kline_to_bar(
        row, symbol="BTCUSDT", bar_interval="1m", underlying_group_id="crypto:BTC"
    )
    assert bar.venue == "binance"
    assert bar.instrument_id == "binance:BTCUSDT"
    assert bar.ts == 1762476180  # ms → s
    assert bar.open == 60000.0
    assert bar.close == 60050.0
    assert bar.volume == 12.5
    assert bar.notional_usd == 750000.0
    assert bar.trade_count == 42
    assert bar.source == "binance_rest"


def test_kline_row_too_short_raises() -> None:
    with pytest.raises(ValueError):
        binance_kline_to_bar(
            [1, 2, 3], symbol="BTCUSDT", bar_interval="1m", underlying_group_id="crypto:BTC"
        )


@pytest.mark.asyncio
async def test_fetch_bars_skips_bad_rows() -> None:
    def responder(_req: httpx.Request) -> Any:
        return [
            [1762476180000, "1", "2", "0.5", "1.5", "10", 1, "100", 3, "0", "0", "0"],
            [1, 2, 3],  # too short → skipped
        ]

    transport = _MockTransport(responder)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://testnet.binancefuture.com"
    ) as client:
        bars = await fetch_binance_bars(
            "BTCUSDT", base_url="https://testnet.binancefuture.com", client=client
        )
    assert len(bars) == 1
    assert bars[0].underlying_group_id == "crypto:BTC"


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


@pytest.fixture(autouse=True)
def _fast_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    import polaris.venues.binance.adapter as binance_adapter

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(binance_adapter, "_async_sleep", _no_sleep)


@pytest.mark.asyncio
async def test_market_buy_payload_and_parse() -> None:
    captured: dict[str, list[str]] = {}

    def responder(req: httpx.Request) -> Any:
        if req.url.path == BINANCE_PLACE_ORDER_PATH and req.method == "POST":
            captured.update(parse_qs(req.url.query.decode()))
            return {
                "orderId": 999,
                "clientOrderId": "polarisvb",
                "status": "NEW",
                "symbol": "BTCUSDT",
            }
        return httpx.Response(404, json={"code": -1, "msg": "not found"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        resp = await adapter.place_market_order(
            symbol="BTCUSDT", side="buy", quantity=0.001, client_order_id="polarisvb"
        )
    finally:
        await client.aclose()
    assert isinstance(resp, BinanceOrderResponse)
    assert resp.ok is True
    assert resp.venue_order_id == "999"
    assert captured["symbol"] == ["BTCUSDT"]
    assert captured["side"] == ["BUY"]
    assert captured["type"] == ["MARKET"]
    assert captured["quantity"] == ["0.001"]
    assert captured["newClientOrderId"] == ["polarisvb"]
    assert "signature" in captured


@pytest.mark.asyncio
async def test_market_sell_side_maps_uppercase() -> None:
    captured: dict[str, list[str]] = {}

    def responder(req: httpx.Request) -> Any:
        captured.update(parse_qs(req.url.query.decode()))
        return {"orderId": 5, "clientOrderId": "x", "status": "NEW"}

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        await adapter.place_market_order(
            symbol="BTCUSDT", side="sell", quantity=0.002, client_order_id="x"
        )
    finally:
        await client.aclose()
    assert captured["side"] == ["SELL"]


@pytest.mark.asyncio
async def test_limit_order_payload() -> None:
    captured: dict[str, list[str]] = {}

    def responder(req: httpx.Request) -> Any:
        captured.update(parse_qs(req.url.query.decode()))
        return {"orderId": 7, "clientOrderId": "lim", "status": "NEW"}

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        resp = await adapter.place_order(
            symbol="BTCUSDT",
            side="buy",
            quantity=0.001,
            price=59000.0,
            client_order_id="lim",
        )
    finally:
        await client.aclose()
    assert resp.ok
    assert captured["type"] == ["LIMIT"]
    assert captured["timeInForce"] == ["GTC"]
    assert captured["price"] == ["59000"]


@pytest.mark.asyncio
async def test_limit_order_rejects_non_positive_price() -> None:
    adapter = BinanceFuturesAdapter(api_key="K", secret="S")
    try:
        with pytest.raises(ValueError):
            await adapter.place_order(
                symbol="BTCUSDT", side="buy", quantity=0.001, price=0.0, client_order_id="x"
            )
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_invalid_side_raises() -> None:
    adapter = BinanceFuturesAdapter(api_key="K", secret="S")
    try:
        with pytest.raises(ValueError):
            await adapter.place_market_order(
                symbol="BTCUSDT", side="long", quantity=0.001, client_order_id="x"
            )
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_error_response_parsed_not_ok() -> None:
    def responder(_req: httpx.Request) -> Any:
        return {"code": -2010, "msg": "Account has insufficient balance"}

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        resp = await adapter.place_market_order(
            symbol="BTCUSDT", side="buy", quantity=0.001, client_order_id="x"
        )
    finally:
        await client.aclose()
    assert resp.ok is False
    assert resp.code == "-2010"
    assert "insufficient" in resp.msg


@pytest.mark.asyncio
async def test_cancel_order_by_client_id() -> None:
    captured: dict[str, list[str]] = {}

    def responder(req: httpx.Request) -> Any:
        assert req.method == "DELETE"
        assert req.url.path == BINANCE_CANCEL_ORDER_PATH
        captured.update(parse_qs(req.url.query.decode()))
        return {"orderId": 999, "status": "CANCELED"}

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        body = await adapter.cancel_order(symbol="BTCUSDT", cl_ord_id="polarisvb")
    finally:
        await client.aclose()
    assert body["status"] == "CANCELED"
    assert captured["origClientOrderId"] == ["polarisvb"]


@pytest.mark.asyncio
async def test_fetch_positions_filters_dicts() -> None:
    def responder(_req: httpx.Request) -> Any:
        return [
            {"symbol": "BTCUSDT", "positionAmt": "0.001", "entryPrice": "60000"},
            {"symbol": "ETHUSDT", "positionAmt": "0.0"},
        ]

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        positions = await adapter.fetch_positions()
    finally:
        await client.aclose()
    assert len(positions) == 2
    assert positions[0]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_fetch_balance_signed_headers_present() -> None:
    captured: dict[str, str] = {}

    def responder(req: httpx.Request) -> Any:
        captured.update(dict(req.headers))
        return {"totalWalletBalance": "1000.0", "assets": []}

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="MYKEY", secret="S", client=client)
    try:
        body = await adapter.fetch_balance()
    finally:
        await client.aclose()
    assert body["totalWalletBalance"] == "1000.0"
    assert captured.get(BINANCE_APIKEY_HEADER.lower()) == "MYKEY"


@pytest.mark.asyncio
async def test_set_leverage() -> None:
    captured: dict[str, list[str]] = {}

    def responder(req: httpx.Request) -> Any:
        captured.update(parse_qs(req.url.query.decode()))
        return {"leverage": 5, "symbol": "BTCUSDT"}

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        body = await adapter.set_leverage(symbol="BTCUSDT", leverage=5)
    finally:
        await client.aclose()
    assert body["leverage"] == 5
    assert captured["leverage"] == ["5"]


@pytest.mark.asyncio
async def test_init_rejects_blank_creds() -> None:
    with pytest.raises(ValueError):
        BinanceFuturesAdapter(api_key="", secret="S")
    with pytest.raises(ValueError):
        BinanceFuturesAdapter(api_key="K", secret="")


# ---------------------------------------------------------------------------
# Order placement 5xx / timeout retry (exponential backoff, idempotent)
# ---------------------------------------------------------------------------


class _SequenceTransport(httpx.AsyncBaseTransport):
    """Returns queued responses/exceptions in order; tracks order-POST count."""

    def __init__(self, order_responses: list[Any], *, lookup_landed: bool = False) -> None:
        self._order_responses = list(order_responses)
        self._lookup_landed = lookup_landed
        self.order_post_count = 0
        self.fetch_order_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == BINANCE_PLACE_ORDER_PATH and request.method == "POST":
            self.order_post_count += 1
            nxt = self._order_responses.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            assert isinstance(nxt, httpx.Response)
            return nxt
        if path == BINANCE_PLACE_ORDER_PATH and request.method == "GET":
            self.fetch_order_count += 1
            if self._lookup_landed:
                return httpx.Response(
                    200, json={"orderId": 555, "clientOrderId": "polarisvb", "status": "NEW"}
                )
            # Not found → Binance returns -2013 with 4xx; emulate empty.
            return httpx.Response(200, json={"code": -2013, "msg": "Order does not exist"})
        return httpx.Response(404, json={"code": -1, "msg": "not found"})


def _ok_order() -> httpx.Response:
    return httpx.Response(
        200, json={"orderId": 777, "clientOrderId": "polarisvb", "status": "NEW"}
    )


@pytest.mark.asyncio
async def test_order_5xx_then_success_retries() -> None:
    transport = _SequenceTransport(
        order_responses=[
            httpx.Response(503, json={"code": -1, "msg": "unavailable"}),
            _ok_order(),
        ]
    )
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        resp = await adapter.place_market_order(
            symbol="BTCUSDT", side="buy", quantity=0.001, client_order_id="polarisvb"
        )
    finally:
        await client.aclose()
    assert resp.ok is True
    assert resp.venue_order_id == "777"
    assert transport.order_post_count == 2


@pytest.mark.asyncio
async def test_order_timeout_then_success_retries() -> None:
    transport = _SequenceTransport(
        order_responses=[httpx.ConnectTimeout("timed out"), _ok_order()]
    )
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        resp = await adapter.place_market_order(
            symbol="BTCUSDT", side="buy", quantity=0.001, client_order_id="polarisvb"
        )
    finally:
        await client.aclose()
    assert resp.ok is True
    assert transport.order_post_count == 2


@pytest.mark.asyncio
async def test_order_persistent_5xx_propagates() -> None:
    transport = _SequenceTransport(
        order_responses=[
            httpx.Response(502, json={"code": -1, "msg": "bad gw"}),
            httpx.Response(502, json={"code": -1, "msg": "bad gw"}),
            httpx.Response(502, json={"code": -1, "msg": "bad gw"}),
        ]
    )
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.place_market_order(
                symbol="BTCUSDT", side="buy", quantity=0.001, client_order_id="polarisvb"
            )
    finally:
        await client.aclose()
    assert transport.order_post_count == 3


@pytest.mark.asyncio
async def test_order_4xx_does_not_retry() -> None:
    transport = _SequenceTransport(
        order_responses=[httpx.Response(400, json={"code": -1102, "msg": "param error"})]
    )
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.place_market_order(
                symbol="BTCUSDT", side="buy", quantity=0.001, client_order_id="polarisvb"
            )
    finally:
        await client.aclose()
    assert transport.order_post_count == 1


@pytest.mark.asyncio
async def test_order_idempotency_landed_not_duplicated() -> None:
    """If the failed POST landed, the clOrdId lookup returns it — no second POST."""
    transport = _SequenceTransport(
        order_responses=[httpx.ReadTimeout("response lost"), _ok_order()],
        lookup_landed=True,
    )
    client = httpx.AsyncClient(transport=transport, base_url="https://testnet.binancefuture.com")
    adapter = BinanceFuturesAdapter(api_key="K", secret="S", client=client)
    try:
        resp = await adapter.place_market_order(
            symbol="BTCUSDT", side="buy", quantity=0.001, client_order_id="polarisvb"
        )
    finally:
        await client.aclose()
    assert transport.order_post_count == 1  # no re-submit
    assert transport.fetch_order_count >= 1
    assert resp.ok is True
    assert resp.venue_order_id == "555"
