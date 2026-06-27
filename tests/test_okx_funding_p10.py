"""OKX funding-rate-history -> per-symbol p10 (weekend_funding_capitulation feed).

DEMO/PAPER only — virtual funds. The funding collector is a pure EVIDENCE source
(it SUGGESTS positioning, never throttles/blocks/sizes). This pins the NEW p10
distribution leg the weekend_funding_capitulation_maker strategy reads:

  * the collector also fetches ``funding-rate-history`` per instrument and
    computes a per-symbol p10 (10th-percentile of the historical funding rates =
    the "extreme-negative / crowded-short" threshold);
  * p10 is CACHED with a long TTL (funding settles on an 8h cycle, so history is
    not refetched on the 5min current-rate cadence — rate-limit / 429 safety);
  * degrade-never-crash: an HTTP error on the history leg leaves the row's p10
    absent (the strategy then reads None -> no emit), never a crash;
  * ``map_altdata_to_market_fields`` selects the PER-SYMBOL funding + p10 for the
    spot symbol under evaluation (spot ``BTC-USDT`` -> perp ``BTC-USDT-SWAP``),
    populating the strategy-visible ``funding_rate_symbol`` / ``funding_rate_p10``
    while the legacy group-MEAN ``funding_rate`` field is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from polaris.core.altdata.okx_funding import (
    OKXFundingCollector,
    compute_funding_p10,
)


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


def _client(
    responder: Callable[[httpx.Request], Any], base_url: str = "https://mock.test"
) -> tuple[httpx.AsyncClient, _MockTransport]:
    transport = _MockTransport(responder)
    return httpx.AsyncClient(transport=transport, base_url=base_url), transport


# A 11-point history whose 10th percentile (linear interp) is a deep-negative
# funding rate. Sorted ascending the p10 sits near the most-negative tail.
_HIST_RATES = [
    "-0.0030", "-0.0021", "-0.0009", "-0.0004", "0.0001",
    "0.0003", "0.0006", "0.0010", "0.0015", "0.0021", "0.0030",
]


def _hist_payload() -> dict[str, Any]:
    return {
        "code": "0",
        "data": [{"fundingRate": r} for r in _HIST_RATES],
    }


# ── compute_funding_p10 (pure) ───────────────────────────────────────────────


def test_compute_p10_is_deep_negative_tail() -> None:
    rates = [float(r) for r in _HIST_RATES]
    p10 = compute_funding_p10(rates)
    assert p10 is not None
    # 10th percentile of this sample is in the negative tail (crowded-short).
    assert p10 < 0.0
    # and it is no more extreme than the minimum (a real percentile).
    assert p10 >= min(rates)


def test_compute_p10_empty_is_none() -> None:
    assert compute_funding_p10([]) is None


def test_compute_p10_single_point() -> None:
    # A degenerate 1-point sample -> that point (no crash, no manufactured tail).
    assert compute_funding_p10([-0.001]) == pytest.approx(-0.001)


# ── collector wires history -> per-instId p10 ────────────────────────────────


@pytest.mark.asyncio
async def test_collector_attaches_p10_per_instrument() -> None:
    def responder(req: httpx.Request) -> Any:
        # history path contains "funding-rate-history"; check it FIRST because
        # "funding-rate" is a substring of it.
        if "funding-rate-history" in req.url.path:
            return _hist_payload()
        if "funding-rate" in req.url.path:
            return {
                "code": "0",
                "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "-0.0025"}],
            }
        if "open-interest" in req.url.path:
            return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "oi": "100.0"}]}
        return httpx.Response(404, json={"code": "1"})

    coll = OKXFundingCollector(instruments=("BTC-USDT-SWAP",))
    client, _ = _client(responder)
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out is not None
    row = out["BTC-USDT-SWAP"]
    assert row["fundingRate"] == pytest.approx(-0.0025)
    assert "p10" in row and row["p10"] is not None
    assert row["p10"] < 0.0


@pytest.mark.asyncio
async def test_p10_is_cached_not_refetched() -> None:
    """The history leg is cached (long TTL) — a 2nd fetch does NOT re-request it."""
    def responder(req: httpx.Request) -> Any:
        if "funding-rate-history" in req.url.path:
            return _hist_payload()
        if "funding-rate" in req.url.path:
            return {
                "code": "0",
                "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "-0.0025"}],
            }
        if "open-interest" in req.url.path:
            return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "oi": "100.0"}]}
        return httpx.Response(404, json={"code": "1"})

    coll = OKXFundingCollector(instruments=("BTC-USDT-SWAP",))
    client, transport = _client(responder)
    await coll.fetch(client=client)
    hist_calls_first = sum(
        1 for c in transport.calls if "funding-rate-history" in c.url.path
    )
    await coll.fetch(client=client)
    hist_calls_total = sum(
        1 for c in transport.calls if "funding-rate-history" in c.url.path
    )
    await client.aclose()
    assert hist_calls_first == 1
    # 2nd fetch served p10 from cache -> no additional history request.
    assert hist_calls_total == 1


@pytest.mark.asyncio
async def test_history_error_degrades_no_crash() -> None:
    """A 500 on the history leg -> row without p10 (no crash); current rate stays."""
    def responder(req: httpx.Request) -> Any:
        if "funding-rate-history" in req.url.path:
            return httpx.Response(500, json={"code": "1"})
        if "funding-rate" in req.url.path:
            return {
                "code": "0",
                "data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "-0.0025"}],
            }
        if "open-interest" in req.url.path:
            return {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "oi": "100.0"}]}
        return httpx.Response(404, json={"code": "1"})

    coll = OKXFundingCollector(instruments=("BTC-USDT-SWAP",))
    client, _ = _client(responder)
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out is not None
    row = out["BTC-USDT-SWAP"]
    assert row["fundingRate"] == pytest.approx(-0.0025)
    assert row.get("p10") is None  # history failed -> p10 absent (neutral no-op)


# ── per-symbol mapping into the strategy-visible AltDataView ──────────────────


def test_map_altdata_selects_per_symbol_funding_and_p10() -> None:
    from polaris.scripts._production_indicators import map_altdata_to_market_fields

    class _Cache:
        def get_for_group(self, group: str, *, now_ts: float | None = None) -> Any:
            return {
                "okx_funding": {
                    "BTC-USDT-SWAP": {"fundingRate": -0.0025, "oi": 100.0, "p10": -0.0019},
                    "ETH-USDT-SWAP": {"fundingRate": 0.0004, "oi": 50.0, "p10": -0.0011},
                }
            }

    view = map_altdata_to_market_fields("crypto:BTC", _Cache(), symbol="BTC-USDT")
    # legacy group-MEAN funding_rate unchanged (mean of -0.0025 and 0.0004).
    assert view.funding_rate == pytest.approx((-0.0025 + 0.0004) / 2)
    # NEW per-symbol selectors resolve the BTC perp row.
    assert view.funding_rate_symbol == pytest.approx(-0.0025)
    assert view.funding_rate_p10 == pytest.approx(-0.0019)


def test_map_altdata_unknown_symbol_neutral() -> None:
    from polaris.scripts._production_indicators import map_altdata_to_market_fields

    class _Cache:
        def get_for_group(self, group: str, *, now_ts: float | None = None) -> Any:
            return {"okx_funding": {"BTC-USDT-SWAP": {"fundingRate": -0.0025, "p10": -0.0019}}}

    # A symbol with no funding row -> per-symbol fields stay None (neutral no-op).
    view = map_altdata_to_market_fields("crypto:DOGE", _Cache(), symbol="DOGE-USDT")
    assert view.funding_rate_symbol is None
    assert view.funding_rate_p10 is None
