"""Tests for the DefiLlama stablecoin-liquidity collector (frontgate-scan #2).

DEMO/PAPER only — virtual funds. Pure EVIDENCE source: a network/parse error
returns ``{}`` (graceful, never a defensive halt). No live network — the
DefiLlama endpoint is exercised via an injected ``httpx`` MockTransport.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from polaris.core.altdata.defillama_stables import (
    DefiLlamaStablesCollector,
    build_entry,
)
from polaris.storage.schema import init_db


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responder: Callable[[httpx.Request], Any]) -> None:
        self._responder = responder

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = self._responder(request)
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)


def _client(responder: Callable[[httpx.Request], Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_MockTransport(responder), base_url="https://mock.test")


def _asset(symbol: str, cur: float, prev_day: float, prev_week: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "circulating": {"peggedUSD": cur},
        "circulatingPrevDay": {"peggedUSD": prev_day},
        "circulatingPrevWeek": {"peggedUSD": prev_week},
    }


# ── pure build_entry ─────────────────────────────────────────────────────


def test_build_entry_positive_net_mint() -> None:
    entry = build_entry(110.0, 105.0, 100.0)
    assert entry["net_mint_7d_usd"] == pytest.approx(10.0)
    assert entry["net_mint_7d_pct"] == pytest.approx(10.0)


def test_build_entry_missing_values_none_not_crash() -> None:
    entry = build_entry(None, None, None)
    assert entry["mcap_usd"] is None
    assert entry["net_mint_7d_usd"] is None
    assert entry["net_mint_7d_pct"] is None


def test_build_entry_zero_prev_week_no_div_by_zero() -> None:
    entry = build_entry(10.0, 10.0, 0.0)
    assert entry["net_mint_7d_usd"] == pytest.approx(10.0)
    assert entry["net_mint_7d_pct"] is None  # would-be div-by-zero -> None, not crash


# ── collector ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_filters_to_tracked_symbols_plus_total() -> None:
    def responder(req: httpx.Request) -> Any:
        return {
            "peggedAssets": [
                _asset("USDT", 200.0, 199.0, 195.0),
                _asset("USDC", 100.0, 99.0, 98.0),
                _asset("FRAX", 1.0, 1.0, 1.0),  # not tracked -> excluded from per-symbol keys
            ]
        }

    coll = DefiLlamaStablesCollector()
    client = _client(responder)
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out is not None
    assert set(out) == {"USDT", "USDC", "TOTAL"}
    assert out["USDT"]["mcap_usd"] == pytest.approx(200.0)
    # TOTAL sums ALL assets, including the untracked FRAX.
    assert out["TOTAL"]["mcap_usd"] == pytest.approx(301.0)


@pytest.mark.asyncio
async def test_fetch_persists_snapshot_rows(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")

    def responder(req: httpx.Request) -> Any:
        return {"peggedAssets": [_asset("USDT", 200.0, 199.0, 195.0)]}

    coll = DefiLlamaStablesCollector(conn=conn)
    client = _client(responder)
    await coll.fetch(client=client)
    await client.aclose()

    rows = conn.execute(
        "SELECT symbol, mcap_usd FROM stablecoin_liquidity ORDER BY symbol"
    ).fetchall()
    assert rows == [("TOTAL", 200.0), ("USDT", 200.0)]
    conn.close()


@pytest.mark.asyncio
async def test_empty_pegged_assets_returns_empty() -> None:
    coll = DefiLlamaStablesCollector()
    client = _client(lambda req: {"peggedAssets": []})
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out == {}


@pytest.mark.asyncio
async def test_http_error_returns_empty_gracefully() -> None:
    coll = DefiLlamaStablesCollector()
    client = _client(lambda req: httpx.Response(500, json={}))
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out == {}
