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
    OKX_PLACE_ALGO_ORDER_PATH,
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
# Min-size clamp-up (Jin 2026-06-23) — replaces the param-reject per-symbol
# cooldown. A sub-min sized qty is BUMPED UP to min_sz (rounded to lotSz) so the
# order FLOWS at the venue minimum instead of being skipped/rejected. A qty
# already >= min is byte-identical (clamp is a no-op above min). This is a venue
# floor applied AFTER sizing — it never enters the T4 multiplier chain.
# ---------------------------------------------------------------------------


def test_clamp_up_to_min_below_min_bumps_to_min_rounded_to_lot() -> None:
    from polaris.venues.okx.constraint_translator import clamp_up_to_min

    # min_sz 0.01, lot_sz 0.01 — a sub-min qty bumps UP to exactly min.
    assert clamp_up_to_min(0.003, min_sz=0.01, lot_sz=0.01) == pytest.approx(0.01)
    # min_sz not a clean lot multiple → round the floor UP to the next lot so the
    # submitted qty is >= min AND a valid lot multiple (never submits below min).
    bumped = clamp_up_to_min(0.0005, min_sz=0.0007, lot_sz=0.0005)
    assert bumped >= 0.0007
    assert bumped == pytest.approx(0.001)  # 0.0007 → next lot multiple 0.001


def test_clamp_up_to_min_above_min_is_noop() -> None:
    from polaris.venues.okx.constraint_translator import clamp_up_to_min

    # Already >= min → returned UNCHANGED (byte-identical, no clamp).
    assert clamp_up_to_min(0.05, min_sz=0.01, lot_sz=0.01) == 0.05
    # Exactly at min → unchanged.
    assert clamp_up_to_min(0.01, min_sz=0.01, lot_sz=0.01) == 0.01
    # No constraint (min_sz=0) → unchanged.
    assert clamp_up_to_min(0.003, min_sz=0.0, lot_sz=0.0) == 0.003


@pytest.mark.asyncio
async def test_round_px_sz_clamps_below_min_up_to_min() -> None:
    """The submit-path chokepoint ``_round_px_sz`` bumps a sub-min base_qty UP to
    min_sz (rounded to lotSz) so the order flows at the venue minimum — replaces
    the per-symbol param-reject cooldown (flow_not_block, aggressive: never
    smaller, never blocked)."""
    client = httpx.AsyncClient(
        transport=_MockTransport(lambda _r: {"code": "0", "data": []}),
        base_url="https://us.okx.com",
    )
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    adapter.set_instrument_constraints(
        {
            "ADA-USDT": InstrumentConstraint(
                inst_id="ADA-USDT", base_ccy="ADA", quote_ccy="USDT",
                lot_sz=0.01, min_sz=0.01, tick_sz=0.0001, state="live",
            )
        }
    )
    try:
        # base_qty 0.003 < min_sz 0.01 → clamped UP to 0.01 (the venue minimum).
        _px, qty = adapter._round_px_sz("ADA-USDT", px=0.5, base_qty=0.003)
    finally:
        await client.aclose()
    assert qty == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_round_px_sz_above_min_byte_identical() -> None:
    """An order already >= min_sz is rounded to lotSz only (clamp is a no-op)."""
    client = httpx.AsyncClient(
        transport=_MockTransport(lambda _r: {"code": "0", "data": []}),
        base_url="https://us.okx.com",
    )
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    adapter.set_instrument_constraints(
        {
            "ETH-USDT": InstrumentConstraint(
                inst_id="ETH-USDT", base_ccy="ETH", quote_ccy="USDT",
                lot_sz=0.01, min_sz=0.01, tick_sz=0.1, state="live",
            )
        }
    )
    try:
        _px, qty = adapter._round_px_sz("ETH-USDT", px=3000.0, base_qty=0.12345)
    finally:
        await client.aclose()
    # 0.12345 floored to lotSz 0.01 = 0.12 (well above min 0.01 → no clamp).
    assert qty == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_ioc_order_submits_clamped_min_sz() -> None:
    """End-to-end: a sub-min IOC order submits ``sz == min_sz`` (the order flows
    at the venue minimum) instead of being rejected 51020 below-min."""
    captured: dict[str, Any] = {}

    def responder(req: httpx.Request) -> Any:
        if req.url.path == OKX_PLACE_ORDER_PATH:
            captured["body"] = json.loads(req.content.decode())
            return {
                "code": "0", "msg": "",
                "data": [{"ordId": "1", "clOrdId": captured["body"]["clOrdId"],
                          "sCode": "0"}],
            }
        return httpx.Response(404, json={"code": "1"})

    client = httpx.AsyncClient(
        transport=_MockTransport(responder), base_url="https://us.okx.com"
    )
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    adapter.set_instrument_constraints(
        {
            "ADA-USDT": InstrumentConstraint(
                inst_id="ADA-USDT", base_ccy="ADA", quote_ccy="USDT",
                lot_sz=0.01, min_sz=0.01, tick_sz=0.0001, state="live",
            )
        }
    )
    try:
        # notional 0.001 USDT / px ~0.5 → base_qty ~0.002 << min_sz 0.01.
        resp = await adapter.place_market_order(
            inst_id="ADA-USDT", side="buy", notional_usd=0.001,
            client_order_id="polarisclamp", ord_type="ioc", last_price_hint=0.5,
        )
    finally:
        await client.aclose()
    assert resp.ok
    assert float(captured["body"]["sz"]) == pytest.approx(0.01)  # clamped UP to min


def test_t4_sizing_chain_unchanged_by_min_clamp(memdb: Any) -> None:
    """The min-size clamp is a VENUE FLOOR applied AFTER the T4 sizing chain — it
    must NOT alter the T4 sizing output. Proof: the full T4 chain
    (``compute_size``: continuous_scalar × tier_amplifier × cell_routing × caps ×
    headroom) yields its ``final_notional_usd`` PURELY from the formula, and the
    venue floor lives entirely downstream in ``_round_px_sz`` — a pure
    ``(px, base_qty)`` function with NO path back into sizing (it never
    imports/calls compute_size, so it cannot enter the multiplier chain → the
    9-stack invariant is intact). The clamp only raises the SUBMIT ``sz`` toward
    min; the T4 notional that feeds the adapter is never rewritten."""
    from polaris.core.sizing.engine import SignalIntent, compute_size
    from polaris.core.sizing.schema import PortfolioState, StrategyRiskState

    conn = memdb
    intent = SignalIntent(
        signal_id="sig-clamp", venue="okx", symbol="ADA-USDT",
        instrument_id="okx:ADA-USDT", underlying_group_id="crypto:ADA",
        asset_class="crypto", strategy="tsmom", track="A", regime="bull_trend",
        direction="long", signal_strength=1.2, listing_age_hours=72.0,
        leverage=1.0, base_risk_pct=0.02,
    )
    risk = StrategyRiskState(
        venue="okx", strategy="tsmom", closed_trades=25, kelly_p=0.55,
        kelly_q=0.45, kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
        updated_ts=0,
    )
    portfolio = PortfolioState(
        equity_usd=10_000.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0}, open_positions=[],
        fill_rate_active_cut=False,
    )
    sized = compute_size(
        conn, intent=intent, risk_state=risk, portfolio=portfolio, now_ts=100,
    )
    t4_notional = sized.final_notional_usd

    from polaris.venues.okx.constraint_translator import clamp_up_to_min

    # An ABOVE-min sized qty is byte-identical through the clamp (no-op above min).
    px = 0.5
    sized_qty = t4_notional / px  # >> min 0.01 → clamp must NOT touch it
    assert clamp_up_to_min(sized_qty, min_sz=0.01, lot_sz=0.01) == sized_qty
    # A genuinely sub-min qty is floored UP to min — and re-running the FULL T4
    # chain afterwards yields the IDENTICAL notional (the floor never fed back).
    assert clamp_up_to_min(0.001, min_sz=0.01, lot_sz=0.01) == pytest.approx(0.01)
    sized_again = compute_size(
        conn, intent=intent, risk_state=risk, portfolio=portfolio, now_ts=100,
    )
    assert sized_again.final_notional_usd == t4_notional  # T4 chain untouched


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
async def test_conditional_stop_body_omits_tgtccy() -> None:
    """The venue-resting conditional SELL stop must NOT send ``tgtCcy`` — OKX
    rejects it with HTTP 400 ``51000 Parameter tgtCcy error`` for an algo SELL
    (tgtCcy is only valid for a spot market BUY). Confirmed live against
    us.okx.com demo: with tgtCcy → 400; without → accepted. This locks the
    correct body so the resting stop actually places (precise-exit restored)."""

    def responder(req: httpx.Request) -> Any:
        if req.url.path == OKX_PLACE_ALGO_ORDER_PATH:
            sent = json.loads(req.content.decode())
            assert "tgtCcy" not in sent  # the bug: this triggered the 400
            assert sent["ordType"] == "conditional"
            assert sent["side"] == "sell"
            assert sent["slTriggerPx"] == "98.5"
            assert sent["slOrdPx"] == "-1"
            return {
                "code": "0",
                "data": [
                    {
                        "algoId": "777",
                        "algoClOrdId": sent["algoClOrdId"],
                        "sCode": "0",
                        "sMsg": "",
                    }
                ],
                "msg": "",
            }
        return httpx.Response(404, json={"code": "1"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.place_conditional_stop(
            inst_id="ETH-USDT",
            side="sell",
            base_qty=0.8,
            trigger_px=98.5,
            client_order_id="polstopeth",
        )
    finally:
        await client.aclose()
    assert resp.ok is True
    assert resp.algo_id == "777"


@pytest.mark.asyncio
async def test_conditional_stop_400_surfaces_real_okx_code() -> None:
    """An HTTP 400 algo reject carries the real OKX code/msg in the body. The
    adapter must SURFACE that code (not swallow it behind a generic
    ``algo_unavailable`` transport label) so the true reason is diagnosable.
    flow_not_block preserved: still ``ok=False`` → software stop is the backstop."""

    def responder(req: httpx.Request) -> Any:
        if req.url.path == OKX_PLACE_ALGO_ORDER_PATH:
            return httpx.Response(
                400,
                json={"code": "51000", "data": [], "msg": "Parameter tgtCcy error"},
            )
        return httpx.Response(404, json={"code": "1"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.place_conditional_stop(
            inst_id="ETH-USDT",
            side="sell",
            base_qty=0.8,
            trigger_px=98.5,
            client_order_id="polstopeth",
        )
    finally:
        await client.aclose()
    assert resp.ok is False  # flow_not_block: software stop remains the backstop
    assert resp.code == "51000"  # real OKX code surfaced, not generic swallow
    assert "tgtCcy" in resp.msg


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
async def test_order_4xx_okx_body_returns_reject_no_retry() -> None:
    """A 4xx carrying an OKX business body (code 51000) is NOT retried AND is
    parsed into a venue REJECT (ok=False) rather than raised.

    Entry-stall root-cause fix (Jin 2026-06-22): a precision/param 400 used to
    re-raise → FAULT_EXCEPTION → permanent HARD_HALT. It now flows as an
    external no-fill so the strategy keeps flowing (flow_not_block). Still no
    retry on 4xx.
    """
    transport = _SequenceTransport(
        order_responses=[
            httpx.Response(
                400,
                json={
                    "code": "1", "msg": "param error",
                    "data": [{"sCode": "51000", "sMsg": "param error"}],
                },
            )
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
    assert resp.ok is False
    assert resp.code == "51000"
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


# ---------------------------------------------------------------------------
# Venue-resting conditional stop (largest unaddressed loss hole — the OKX
# SPOT inter-tick gap that lets a soft stop get gapped through to a -34..-100R
# orphan). A resting algo/conditional order triggers VENUE-side before the
# next software poll. PRECISE-EXIT loss-defence, never a throttle/size-cut.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conditional_stop_submits_resting_order_rounded() -> None:
    """place_conditional_stop submits an ordType=conditional resting order with
    slTriggerPx rounded to tickSz and sz floored to lotSz, returns algoId."""
    from polaris.venues.okx.adapter import (
        OKX_PLACE_ALGO_ORDER_PATH,
        OKXAlgoOrderResponse,
    )

    captured: dict[str, Any] = {}

    def responder(req: httpx.Request) -> Any:
        if req.url.path == OKX_PLACE_ALGO_ORDER_PATH:
            captured["body"] = json.loads(req.content.decode())
            return {
                "code": "0",
                "msg": "",
                "data": [{"algoId": "algo-7", "algoClOrdId": "polstopP1", "sCode": "0", "sMsg": ""}],
            }
        return httpx.Response(404, json={"code": "1", "msg": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    adapter.set_instrument_constraints(
        {
            "ETH-USDT": InstrumentConstraint(
                inst_id="ETH-USDT", base_ccy="ETH", quote_ccy="USDT",
                lot_sz=0.01, min_sz=0.01, tick_sz=0.1, state="live",
            )
        }
    )
    try:
        resp = await adapter.place_conditional_stop(
            inst_id="ETH-USDT",
            side="sell",
            base_qty=0.12345,
            trigger_px=2999.97,  # → rounds DOWN to tick 0.1 = 2999.9
            client_order_id="polstop-P1",
        )
    finally:
        await client.aclose()
    assert isinstance(resp, OKXAlgoOrderResponse)
    assert resp.ok is True
    assert resp.algo_id == "algo-7"
    body = captured["body"]
    assert body["ordType"] == "conditional"
    assert body["side"] == "sell"
    assert body["tdMode"] == "cash"
    # trigger px rounded to tickSz (0.1) — 2999.97 → 2999.9
    assert float(body["slTriggerPx"]) == 2999.9
    # sz floored to lotSz (0.01) — 0.12345 → 0.12
    assert float(body["sz"]) == 0.12
    # market on trigger (never a resting limit that can miss)
    assert body["slOrdPx"] == "-1"
    # clOrdId sanitized (no hyphen)
    assert "-" not in body["algoClOrdId"]


@pytest.mark.asyncio
async def test_conditional_stop_4xx_falls_back_no_raise() -> None:
    """A 4xx OKX reject (algo unavailable for symbol) returns ok=False WITHOUT
    raising — the caller keeps the software stop (flow_not_block, never blocks)."""
    from polaris.venues.okx.adapter import OKX_PLACE_ALGO_ORDER_PATH

    def responder(req: httpx.Request) -> Any:
        if req.url.path == OKX_PLACE_ALGO_ORDER_PATH:
            return httpx.Response(
                400,
                json={"code": "1", "msg": "algo not supported",
                      "data": [{"sCode": "51280", "sMsg": "algo not supported"}]},
            )
        return httpx.Response(404, json={"code": "1", "msg": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.place_conditional_stop(
            inst_id="DOGE-USDT", side="sell", base_qty=100.0,
            trigger_px=0.1, client_order_id="polstopX",
        )
    finally:
        await client.aclose()
    assert resp.ok is False
    assert resp.algo_id is None


@pytest.mark.asyncio
async def test_conditional_stop_transport_error_falls_back_no_raise() -> None:
    """A transport blip on the algo POST returns ok=False (never raises) so the
    software stop remains the backstop — the resting stop is best-effort."""
    from polaris.venues.okx.adapter import OKX_PLACE_ALGO_ORDER_PATH

    def responder(req: httpx.Request) -> Any:
        if req.url.path == OKX_PLACE_ALGO_ORDER_PATH:
            raise httpx.ConnectError("boom")
        return httpx.Response(404, json={"code": "1", "msg": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.place_conditional_stop(
            inst_id="BTC-USDT", side="sell", base_qty=0.001,
            trigger_px=60000.0, client_order_id="polstopY",
        )
    finally:
        await client.aclose()
    assert resp.ok is False
    assert resp.algo_id is None


@pytest.mark.asyncio
async def test_cancel_algo_order_posts_list() -> None:
    """cancel_algo_order POSTs a LIST of {instId, algoId} to cancel-algos."""
    from polaris.venues.okx.adapter import OKX_CANCEL_ALGO_ORDER_PATH

    captured: dict[str, Any] = {}

    def responder(req: httpx.Request) -> Any:
        if req.url.path == OKX_CANCEL_ALGO_ORDER_PATH:
            captured["body"] = json.loads(req.content.decode())
            return {"code": "0", "data": [{"algoId": "algo-7", "sCode": "0"}]}
        return httpx.Response(404, json={"code": "1", "msg": "nf"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        body = await adapter.cancel_algo_order(inst_id="ETH-USDT", algo_id="algo-7")
    finally:
        await client.aclose()
    assert body["code"] == "0"
    sent = captured["body"]
    assert isinstance(sent, list)
    assert sent[0]["algoId"] == "algo-7"
    assert sent[0]["instId"] == "ETH-USDT"


@pytest.mark.asyncio
async def test_fetch_algo_order_by_clordid_sanitizes_and_parses() -> None:
    """fetch_algo_order GETs /trade/order-algo by algoClOrdId, sanitizing the key
    IDENTICALLY to the place leg so the lookup matches what was submitted, and
    parses data[0] into an OKXAlgoOrderResponse (algoId recovered for adoption)."""
    captured: dict[str, Any] = {}
    raw_clordid = "polstoppos-abc"

    def responder(req: httpx.Request) -> Any:
        if req.url.path == OKX_PLACE_ALGO_ORDER_PATH and req.method == "GET":
            captured["query"] = dict(req.url.params)
            return {
                "code": "0",
                "msg": "",
                "data": [
                    {
                        "algoId": "orphan-9",
                        "algoClOrdId": sanitize_clordid(raw_clordid),
                        "slTriggerPx": "97.0",
                        "state": "live",
                    }
                ],
            }
        return httpx.Response(404, json={"code": "1"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.fetch_algo_order(
            inst_id="ETH-USDT", algo_cl_ord_id=raw_clordid
        )
    finally:
        await client.aclose()
    assert resp.ok is True
    assert resp.algo_id == "orphan-9"
    # The GET key is the SANITIZED clOrdId (byte-identical to the place leg).
    assert captured["query"]["algoClOrdId"] == sanitize_clordid(raw_clordid)
    assert captured["query"]["instId"] == "ETH-USDT"
    assert resp.raw["data"][0]["slTriggerPx"] == "97.0"


@pytest.mark.asyncio
async def test_fetch_algo_order_transport_error_fails_open() -> None:
    """A transport blip on the recovery GET returns ok=False (never raises) — the
    caller keeps the software stop as the backstop (flow_not_block)."""

    def responder(req: httpx.Request) -> Any:
        return httpx.Response(500, json={"code": "1", "msg": "boom"})

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        resp = await adapter.fetch_algo_order(
            inst_id="ETH-USDT", algo_cl_ord_id="polstoppos-x"
        )
    finally:
        await client.aclose()
    assert resp.ok is False
    assert resp.algo_id is None


@pytest.mark.asyncio
async def test_fetch_algo_order_requires_a_key() -> None:
    """Neither algoId nor algoClOrdId → ValueError (no unfiltered GET)."""
    client = httpx.AsyncClient(
        transport=_MockTransport(lambda r: {"code": "0", "data": []}),
        base_url="https://us.okx.com",
    )
    adapter = OKXAdapter(api_key="K", secret="S", passphrase="P", client=client)
    try:
        with pytest.raises(ValueError, match="algo_id or algo_cl_ord_id"):
            await adapter.fetch_algo_order(inst_id="ETH-USDT")
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Candles 429 pacing + backoff (the OKX "Too Many" storm root-cause fix)
# ---------------------------------------------------------------------------


def _candles_ok(rows: list[list[str]] | None = None) -> httpx.Response:
    data = rows if rows is not None else [
        ["1700000000000", "100", "110", "90", "105", "12", "1200"],
    ]
    return httpx.Response(200, json={"code": "0", "data": data})


@pytest.fixture
def _reset_candles_bucket() -> Any:
    """Give each test a fresh, full candles bucket (no cross-test drain)."""
    import polaris.venues.okx.adapter as okx_adapter
    from polaris.core.ratelimit import AsyncTokenBucket

    prev = okx_adapter._CANDLES_BUCKET
    okx_adapter._CANDLES_BUCKET = AsyncTokenBucket(
        rate=okx_adapter.CANDLES_RATE, per_sec=okx_adapter.CANDLES_PER_SEC
    )
    yield
    okx_adapter._CANDLES_BUCKET = prev


@pytest.mark.asyncio
async def test_candles_429_then_success_retries(
    monkeypatch: pytest.MonkeyPatch, _reset_candles_bucket: Any
) -> None:
    """A 429 on candles is absorbed by bounded backoff; the retry succeeds.

    ROOT CAUSE of the OKX storm: a 429 used to raise straight through to the
    bar-ingest caller, which dropped the bar with no retry. Now fetch_okx_bars
    retries with backoff (honouring Retry-After) so the bar flows.
    """
    import polaris.venues.okx.adapter as okx_adapter
    from polaris.venues.okx.adapter import fetch_okx_bars

    sleeps: list[float] = []

    async def _capture_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(okx_adapter, "_async_sleep", _capture_sleep)

    seq = [
        httpx.Response(429, headers={"Retry-After": "0.7"}, text="Too Many Requests"),
        _candles_ok(),
    ]

    def responder(req: httpx.Request) -> Any:
        return seq.pop(0)

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    try:
        bars = await fetch_okx_bars("BTC-USDT", bar_interval="1D", limit=240, client=client)
    finally:
        await client.aclose()
    assert len(bars) == 1  # the retry delivered the bar
    assert sleeps == [0.7]  # honoured Retry-After, exactly one backoff


@pytest.mark.asyncio
async def test_candles_persistent_429_raises_for_caller_degrade(
    monkeypatch: pytest.MonkeyPatch, _reset_candles_bucket: Any
) -> None:
    """Exhausted 429 retries re-raise so the caller degrades to [] (no crash).

    The bar-ingest caller (_production_bars) catches HTTPError → returns [] →
    the symbol simply has no fresh history this cadence (flow_not_block). The
    live price path is untouched.
    """
    import polaris.venues.okx.adapter as okx_adapter
    from polaris.venues.okx.adapter import fetch_okx_bars

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(okx_adapter, "_async_sleep", _no_sleep)

    def responder(req: httpx.Request) -> Any:
        return httpx.Response(429, text="Too Many Requests")

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_okx_bars("BTC-USDT", bar_interval="1D", limit=240, client=client)
    finally:
        await client.aclose()
    # initial + CANDLES_RETRY_MAX attempts all 429.
    assert len(transport.calls) == okx_adapter.CANDLES_RETRY_MAX + 1


@pytest.mark.asyncio
async def test_candles_acquires_token_before_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_okx_bars paces through the candles token bucket before the GET.

    Proves the bucket is on the hot path: a stub bucket records each acquire and
    the GET only fires after the token is taken (pacing precedes the network).
    """
    import polaris.venues.okx.adapter as okx_adapter
    from polaris.venues.okx.adapter import fetch_okx_bars

    events: list[str] = []

    class _StubBucket:
        async def acquire(self, *, timeout: float | None = None) -> bool:
            events.append("acquire")
            return True

    monkeypatch.setattr(okx_adapter, "_CANDLES_BUCKET", _StubBucket())

    def responder(req: httpx.Request) -> Any:
        events.append("get")
        return _candles_ok()

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    try:
        bars = await fetch_okx_bars("ETH-USDT", bar_interval="1D", limit=240, client=client)
    finally:
        await client.aclose()
    assert len(bars) == 1
    assert events == ["acquire", "get"]  # paced once, before the network GET


@pytest.mark.asyncio
async def test_candles_soft_cap_proceeds_token_less_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drained bucket (acquire→False) still issues the GET (soft cap, no drop).

    flow_not_block: when the bounded pacing wait elapses with no token, the bar
    fetch PROCEEDS token-less rather than dropping the bar. Proves the False
    return is treated as a soft cap, not a hard defer — the bar still flows.
    """
    import polaris.venues.okx.adapter as okx_adapter
    from polaris.venues.okx.adapter import fetch_okx_bars

    class _DrainedBucket:
        async def acquire(self, *, timeout: float | None = None) -> bool:
            return False  # always "timed out" — no token ever available

    monkeypatch.setattr(okx_adapter, "_CANDLES_BUCKET", _DrainedBucket())

    got = {"n": 0}

    def responder(req: httpx.Request) -> Any:
        got["n"] += 1
        return _candles_ok()

    transport = _MockTransport(responder)
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    try:
        bars = await fetch_okx_bars("BTC-USDT", bar_interval="1D", limit=240, client=client)
    finally:
        await client.aclose()
    assert got["n"] == 1  # GET fired despite no token (soft cap)
    assert len(bars) == 1  # bar still delivered (flow_not_block)
