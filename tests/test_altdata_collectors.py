"""Tests for polaris.core.altdata collectors (#6 alt-data EVIDENCE).

DEMO/PAPER only — virtual funds. These collectors are pure data sources:
they SUGGEST regime evidence, never throttle/block/size. A failing or keyless
collector returns ``{}`` (graceful fallback, NOT a defensive halt).

No live network: every collector is exercised with an injected ``httpx``
MockTransport. Keyless collectors must return ``{}`` WITHOUT any network call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from polaris.core.altdata._base import AltDataCollector
from polaris.core.altdata.coinglass import CoinglassCollector
from polaris.core.altdata.crypto_fg import CryptoFearGreedCollector
from polaris.core.altdata.fred_macro import FredMacroCollector
from polaris.core.altdata.myfxbook import MyfxbookCollector
from polaris.core.altdata.okx_funding import OKXFundingCollector

# ── Mock transport (mirror tests/test_okx_adapter.py style) ──────────────────


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responder: Callable[[httpx.Request], Any]) -> None:
        self._responder = responder
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        body = self._responder(request)
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)


class _ExplodingTransport(httpx.AsyncBaseTransport):
    """Any network call raises — used to prove keyless collectors never call."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected network call to {request.url}")


def _client(
    responder: Callable[[httpx.Request], Any], base_url: str = "https://mock.test"
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_MockTransport(responder), base_url=base_url)


# ── ABC contract ─────────────────────────────────────────────────────────────


def test_base_is_abstract() -> None:
    with pytest.raises(TypeError):
        AltDataCollector()  # type: ignore[abstract]


def test_collectors_expose_metadata() -> None:
    for coll in (
        OKXFundingCollector(),
        CryptoFearGreedCollector(),
        FredMacroCollector(api_key="k"),
        CoinglassCollector(),
        MyfxbookCollector(),
    ):
        assert isinstance(coll.name, str) and coll.name
        assert isinstance(coll.ttl_sec, int) and coll.ttl_sec > 0
        assert isinstance(coll.asset_classes, tuple) and coll.asset_classes


# ── OKX funding ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_okx_funding_parses() -> None:
    def responder(req: httpx.Request) -> Any:
        if "funding-rate" in req.url.path:
            return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "0.00025"}]}
        if "open-interest" in req.url.path:
            return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "oi": "12345.6"}]}
        return httpx.Response(404, json={"code": "1"})

    coll = OKXFundingCollector(instruments=("BTC-USDT-SWAP",))
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert "BTC-USDT-SWAP" in out
    row = out["BTC-USDT-SWAP"]
    assert row["fundingRate"] == pytest.approx(0.00025)
    assert row["oi"] == pytest.approx(12345.6)


@pytest.mark.asyncio
async def test_okx_funding_network_error_returns_empty() -> None:
    def responder(req: httpx.Request) -> Any:
        return httpx.Response(500, json={"code": "1"})

    coll = OKXFundingCollector(instruments=("BTC-USDT-SWAP",))
    out = await coll.fetch(client=_client(responder))
    assert out == {}


# ── Crypto Fear & Greed (alt.me) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crypto_fg_parses() -> None:
    data = [
        {"value": "12", "value_classification": "Extreme Fear"},
        *[{"value": "30", "value_classification": "Fear"} for _ in range(5)],
        {"value": "55", "value_classification": "Greed"},
    ]

    def responder(req: httpx.Request) -> Any:
        assert "fng" in req.url.path
        return {"data": data}

    coll = CryptoFearGreedCollector()
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert out["value"] == 12
    assert out["label"] == "Extreme Fear"
    assert out["one_week_ago"] == 55


@pytest.mark.asyncio
async def test_crypto_fg_empty_response_returns_empty() -> None:
    def responder(req: httpx.Request) -> Any:
        return {"data": []}

    coll = CryptoFearGreedCollector()
    out = await coll.fetch(client=_client(responder))
    assert out == {}


# ── FRED macro ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fred_macro_parses() -> None:
    series_latest = {
        "VIXCLS": "18.5",
        "BAMLH0A0HYM2": "3.5",  # percent → 350 bps after normalize
        "MOVE": "95.0",
        "T10Y2Y": "0.42",
    }

    def responder(req: httpx.Request) -> Any:
        sid = req.url.params.get("series_id")
        val = series_latest.get(sid, ".")
        return {"observations": [{"value": val}]}

    coll = FredMacroCollector(api_key="testkey")
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert out["vix"] == pytest.approx(18.5)
    assert out["hy_spread"] == pytest.approx(350.0)  # 3.5% → 350 bps
    assert out["move"] == pytest.approx(95.0)
    assert out["yield_curve"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_fred_macro_no_key_returns_empty_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    coll = FredMacroCollector(api_key=None)
    # ExplodingTransport proves: no key → no network call at all.
    client = httpx.AsyncClient(transport=_ExplodingTransport())
    out = await coll.fetch(client=client)
    assert out == {}
    await client.aclose()


@pytest.mark.asyncio
async def test_fred_macro_dot_observations_skip() -> None:
    """A series returning only '.' (no data yet) yields None for that key."""

    def responder(req: httpx.Request) -> Any:
        sid = req.url.params.get("series_id")
        if sid == "VIXCLS":
            return {"observations": [{"value": "20.0"}]}
        return {"observations": [{"value": "."}]}

    coll = FredMacroCollector(api_key="testkey")
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert out["vix"] == pytest.approx(20.0)
    assert out["hy_spread"] is None


# ── Keyless graceful-skip stubs (Coinglass / MyFxBook) ────────────────────────


@pytest.mark.asyncio
async def test_coinglass_keyless_returns_empty_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    coll = CoinglassCollector(api_key=None)
    client = httpx.AsyncClient(transport=_ExplodingTransport())
    out = await coll.fetch(client=client)
    assert out == {}
    await client.aclose()


@pytest.mark.asyncio
async def test_coinglass_empty_env_key_returns_empty_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # .env has COINGLASS_API_KEY= (empty) → must be treated as absent.
    monkeypatch.setenv("COINGLASS_API_KEY", "")
    coll = CoinglassCollector()
    client = httpx.AsyncClient(transport=_ExplodingTransport())
    out = await coll.fetch(client=client)
    assert out == {}
    await client.aclose()


@pytest.mark.asyncio
async def test_myfxbook_keyless_returns_empty_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MYFXBOOK_EMAIL", raising=False)
    monkeypatch.delenv("MYFXBOOK_PASSWORD", raising=False)
    coll = MyfxbookCollector(email=None, password=None)
    client = httpx.AsyncClient(transport=_ExplodingTransport())
    out = await coll.fetch(client=client)
    assert out == {}
    await client.aclose()


@pytest.mark.asyncio
async def test_myfxbook_empty_env_creds_returns_empty_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYFXBOOK_EMAIL", "")
    monkeypatch.setenv("MYFXBOOK_PASSWORD", "")
    coll = MyfxbookCollector()
    client = httpx.AsyncClient(transport=_ExplodingTransport())
    out = await coll.fetch(client=client)
    assert out == {}
    await client.aclose()
