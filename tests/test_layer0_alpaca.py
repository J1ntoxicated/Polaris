"""Layer 0 — Alpaca (Track C / US-equity) universe fetcher tests.

Mirrors the Capital fetcher contract: env creds, smoke-safe empty return when
missing, builds ``UniverseInstrument`` rows from a mocked ``/v2/assets``
response (status=active, class=us_equity, tradable=true). DEMO/PAPER only —
Track C is additive; OKX/Capital paths are untouched.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from polaris.core.universe._alpaca import (
    ALPACA_PAPER_BASE,
    fetch_alpaca_instruments,
)
from polaris.core.universe.discovery import refresh_alpaca_universe
from polaris.core.universe.schema import UniverseInstrument

NOW = 1_780_000_000


def _assets_payload() -> list[dict[str, object]]:
    """A canned ``GET /v2/assets`` response (Alpaca shape)."""
    return [
        {
            "id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
            "class": "us_equity",
            "exchange": "NASDAQ",
            "symbol": "AAPL",
            "name": "Apple Inc. Common Stock",
            "status": "active",
            "tradable": True,
            "marginable": True,
            "shortable": True,
            "easy_to_borrow": True,
            "fractionable": True,
        },
        {
            "id": "8ccae427-5dd0-45b3-b5fe-7ba5e422c766",
            "class": "us_equity",
            "exchange": "NASDAQ",
            "symbol": "TSLA",
            "name": "Tesla, Inc. Common Stock",
            "status": "active",
            "tradable": True,
            "marginable": True,
            "shortable": True,
            "easy_to_borrow": True,
            "fractionable": True,
        },
        {
            # crypto class — must be filtered out (us_equity only).
            "id": "276e2673-764b-4ab6-a611-caf665ca6340",
            "class": "crypto",
            "exchange": "CRYPTO",
            "symbol": "BTC/USD",
            "name": "Bitcoin",
            "status": "active",
            "tradable": True,
        },
    ]


def _mock_client(
    payload: list[dict[str, object]], *, status_code: int = 200
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        # The P1 liquidity enrichment also hits the data host (screener +
        # snapshots) on the shared client; those return empty here so the rows
        # keep their coarse placeholder (this test pins asset-row building).
        if request.url.path == "/v1beta1/screener/stocks/most-actives":
            return httpx.Response(200, json={"most_actives": []})
        if request.url.path == "/v2/stocks/snapshots":
            return httpx.Response(200, json={"snapshots": {}})
        assert request.url.path == "/v2/assets"
        # The fetcher narrows server-side via query params.
        return httpx.Response(status_code, json=payload)

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(
        base_url=ALPACA_PAPER_BASE, transport=transport, timeout=5.0
    )


def test_fetch_builds_equity_instruments() -> None:
    async def run() -> list[UniverseInstrument]:
        async with _mock_client(_assets_payload()) as cli:
            return await fetch_alpaca_instruments(
                api_key="k", secret_key="s", now_ts=NOW, client=cli
            )

    out = asyncio.run(run())
    symbols = {i.symbol for i in out}
    assert symbols == {"AAPL", "TSLA"}  # crypto class dropped
    for inst in out:
        assert inst.venue == "alpaca"
        assert inst.asset_class == "equity"
        assert inst.state == "live"
        assert inst.quote_ccy == "USD"
        assert inst.instrument_id == f"alpaca:{inst.symbol}"
        assert inst.underlying_group_id == f"equity:{inst.symbol}"
        assert inst.last_seen_ts == NOW


def test_classify_equity_only() -> None:
    async def run() -> list[UniverseInstrument]:
        async with _mock_client(_assets_payload()) as cli:
            return await fetch_alpaca_instruments(
                api_key="k", secret_key="s", now_ts=NOW, client=cli
            )

    out = asyncio.run(run())
    assert all(i.asset_class == "equity" for i in out)
    assert all(i.venue == "alpaca" for i in out)


def test_empty_on_missing_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_SECRET",
        "ARCHIVE_ALPACA_PAPER_API_KEY",
        "ARCHIVE_ALPACA_PAPER_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    out = asyncio.run(fetch_alpaca_instruments(now_ts=NOW))
    assert out == []


def test_archive_creds_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """ARCHIVE_* is the documented fallback when the bare names are unset."""
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_SECRET", raising=False)
    monkeypatch.setenv("ARCHIVE_ALPACA_PAPER_API_KEY", "ak")
    monkeypatch.setenv("ARCHIVE_ALPACA_PAPER_SECRET", "asec")

    async def run() -> list[UniverseInstrument]:
        async with _mock_client(_assets_payload()) as cli:
            return await fetch_alpaca_instruments(now_ts=NOW, client=cli)

    out = asyncio.run(run())
    assert {i.symbol for i in out} == {"AAPL", "TSLA"}


def test_refresh_alias_passes_through() -> None:
    async def run() -> list[UniverseInstrument]:
        async with _mock_client(_assets_payload()) as cli:
            return await refresh_alpaca_universe(
                NOW, api_key="k", secret_key="s", client=cli
            )

    out = asyncio.run(run())
    assert {i.symbol for i in out} == {"AAPL", "TSLA"}
