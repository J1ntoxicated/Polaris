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
async def test_fred_macro_preserves_observation_date() -> None:
    """Currency fix: _latest returns the observation date; fetch surfaces it as
    ``<key>_asof`` so a stale (weekend/holiday) reading is age-labelable downstream.
    """
    series = {
        "VIXCLS": [{"date": "2026-06-26", "value": "18.5"}],
        "BAMLH0A0HYM2": [{"date": "2026-06-26", "value": "3.5"}],
        "MOVE": [{"date": "2026-06-26", "value": "95.0"}],
        "T10Y2Y": [{"date": "2026-06-26", "value": "0.42"}],
    }

    def responder(req: httpx.Request) -> Any:
        sid = req.url.params.get("series_id")
        return {"observations": series.get(sid, [{"value": "."}])}

    coll = FredMacroCollector(api_key="testkey")
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert out["vix"] == pytest.approx(18.5)
    assert out["vix_asof"] == "2026-06-26"
    assert out["hy_asof"] == "2026-06-26"
    assert out["move_asof"] == "2026-06-26"
    assert out["yield_curve_asof"] == "2026-06-26"


@pytest.mark.asyncio
async def test_fred_macro_asof_skips_dot_rows() -> None:
    """``_latest`` returns the date of the FIRST VALID float, not a '.' placeholder."""

    def responder(req: httpx.Request) -> Any:
        sid = req.url.params.get("series_id")
        if sid == "VIXCLS":
            return {
                "observations": [
                    {"date": "2026-06-27", "value": "."},  # not posted yet
                    {"date": "2026-06-26", "value": "19.0"},  # last real print
                ]
            }
        return {"observations": [{"value": "."}]}

    coll = FredMacroCollector(api_key="testkey")
    out = await coll.fetch(client=_client(responder))
    assert out is not None
    assert out["vix"] == pytest.approx(19.0)
    assert out["vix_asof"] == "2026-06-26"
    # A series with no valid float surfaces no asof (None value).
    assert out["hy_spread"] is None
    assert out.get("hy_asof") is None


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


# ── CFTC COT collector (commodity speculative positioning, keyless) ───────────
def _cot_row(name: str, net: float, oi: int = 1_000_000, date: str = "2026-05-26") -> dict:
    # Encode a target net_spec_pct via long/short over a fixed OI.
    return {
        "contract_market_name": name,
        "report_date_as_yyyy_mm_dd": f"{date}T00:00:00",
        "noncomm_positions_long_all": str(int((0.5 + net / 2) * oi)),
        "noncomm_positions_short_all": str(int((0.5 - net / 2) * oi)),
        "open_interest_all": str(oi),
    }


def _cot_series(name: str, latest: float, history: list[float]) -> list[dict]:
    # Newest-first: latest then the trailing weeks (descending dates).
    rows = [_cot_row(name, latest, date="2026-05-26")]
    for i, net in enumerate(history):
        rows.append(_cot_row(name, net, date=f"2026-0{(i % 4) + 1}-0{(i % 9) + 1}"))
    return rows


def test_cot_derive_pctile_mapping_and_change() -> None:
    from polaris.core.altdata.cftc_cot import _derive_signals

    # WTI: latest +0.25 above ALL 30 (dispersed) trailing weeks → pctile 1.0.
    history = [round(0.10 + 0.002 * i, 4) for i in range(30)]  # 0.100..0.158, all < 0.25
    rows = _cot_series("CRUDE OIL, LIGHT SWEET-WTI", 0.25, history)
    out = _derive_signals(rows)
    assert out["OIL_CRUDE"]["net_spec_pctile"] == 1.0
    assert abs(out["OIL_CRUDE"]["net_spec_pct"] - 0.25) < 1e-9
    assert abs(out["OIL_CRUDE"]["net_spec_chg"] - 0.15) < 1e-9  # 0.25 - history[0]=0.10
    assert out["OIL_CRUDE"]["report_date"] == "2026-05-26"


def test_cot_pctile_neutral_at_own_median() -> None:
    from polaris.core.altdata.cftc_cot import _derive_signals

    # GOLD latest +0.44 with history straddling it (half below, half above) →
    # ~0.5 pctile = neutral, even though the RAW level is high (the bias fix).
    history = [0.30] * 15 + [0.55] * 15
    out = _derive_signals(_cot_series("GOLD", 0.44, history))
    assert abs(out["GOLD"]["net_spec_pctile"] - 0.5) < 1e-9


def test_cot_min_history_skipped() -> None:
    from polaris.core.altdata.cftc_cot import _derive_signals

    # Only 20 trailing weeks (< _MIN_HISTORY_WEEKS=26) → no credible percentile.
    out = _derive_signals(_cot_series("GOLD", 0.44, [0.30] * 20))
    assert "GOLD" not in out


def test_cot_unmapped_and_bad_rows_skipped() -> None:
    from polaris.core.altdata.cftc_cot import _derive_signals

    # Unmapped market + a row with missing fields → nothing derived (no mapped
    # market reaches the min-history threshold).
    rows = [
        _cot_row("SOME RANDOM FX FUTURE", 0.1),  # unmapped → absent
        {"contract_market_name": "CORN"},         # missing fields → skipped
    ]
    out = _derive_signals(rows)
    assert out == {}


def test_cot_zero_open_interest_rows_skipped() -> None:
    from polaris.core.altdata.cftc_cot import _derive_signals

    # 30 SILVER weeks but every row has OI=0 → all unparsable → SILVER absent.
    rows = _cot_series("SILVER", 0.2, [0.1] * 30)
    for r in rows:
        r["open_interest_all"] = "0"
    out = _derive_signals(rows)
    assert "SILVER" not in out
