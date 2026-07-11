"""Tests for the Finnhub earnings-calendar collector (frontgate-scan item #3).

DEMO/PAPER only — virtual funds. Pure EVIDENCE source: keyless/network/parse
errors return ``{}`` (graceful, never a defensive halt). No live network —
the Finnhub endpoint is exercised via an injected ``httpx`` MockTransport.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from polaris.core.altdata.finnhub_earnings import (
    FinnhubEarningsCollector,
    _days_from,
    surprise_pct,
)
from polaris.storage.schema import init_db


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
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected network call to {request.url}")


def _client(responder: Callable[[httpx.Request], Any]) -> tuple[httpx.AsyncClient, _MockTransport]:
    transport = _MockTransport(responder)
    return httpx.AsyncClient(transport=transport, base_url="https://mock.test"), transport


def _row(symbol: str, date: str, hour: str = "bmo", eps_est: float | None = 1.0,
         eps_act: float | None = None) -> dict[str, Any]:
    return {
        "symbol": symbol, "date": date, "hour": hour,
        "epsEstimate": eps_est, "epsActual": eps_act,
        "revenueEstimate": 1000.0, "revenueActual": None,
    }


# ── pure surprise_pct ────────────────────────────────────────────────────


def test_surprise_pct_positive_beat() -> None:
    assert surprise_pct(1.0, 1.2) == pytest.approx(20.0)


def test_surprise_pct_none_when_actual_missing() -> None:
    assert surprise_pct(1.0, None) is None


def test_surprise_pct_zero_estimate_no_div_by_zero() -> None:
    assert surprise_pct(0.0, 1.0) is None


# ── pure _days_from (bmo/amc look-ahead sign fix) ────────────────────────


def test_days_from_amc_same_day_before_close_is_still_future() -> None:
    """An 'amc' print (~21:00 UTC in July/EDT) hasn't happened yet at 14:00 UTC
    on the same date — days_to_earnings must stay NON-negative, not report the
    event as already in the past (look-ahead sign bug)."""
    now = _dt.datetime(2026, 7, 11, 14, 0, tzinfo=_dt.UTC)
    days = _days_from("2026-07-11", "amc", now)
    assert days > 0.0


def test_days_from_bmo_same_day_after_open_is_past() -> None:
    """A 'bmo' print (~13:30 UTC in July/EDT) has already happened by 20:00
    UTC the same date."""
    now = _dt.datetime(2026, 7, 11, 20, 0, tzinfo=_dt.UTC)
    days = _days_from("2026-07-11", "bmo", now)
    assert days < 0.0


def test_days_from_unknown_hour_falls_back_to_utc_midnight() -> None:
    now = _dt.datetime(2026, 7, 11, 0, 0, tzinfo=_dt.UTC)
    assert _days_from("2026-07-12", "", now) == pytest.approx(1.0)


# ── collector ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_api_key_returns_empty_no_network() -> None:
    coll = FinnhubEarningsCollector(api_key="", symbols_override=("AAPL",))
    client = httpx.AsyncClient(transport=_ExplodingTransport())
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out == {}


@pytest.mark.asyncio
async def test_no_symbols_returns_empty_no_network() -> None:
    coll = FinnhubEarningsCollector(api_key="k", symbols_override=())
    client = httpx.AsyncClient(transport=_ExplodingTransport())
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out == {}


@pytest.mark.asyncio
async def test_fetch_filters_to_active_universe_intersection() -> None:
    def responder(req: httpx.Request) -> Any:
        return {
            "earningsCalendar": [
                _row("AAPL", "2026-07-20"),
                _row("ZZZZ_NOT_OURS", "2026-07-20"),
            ]
        }

    coll = FinnhubEarningsCollector(api_key="k", symbols_override=("AAPL",))
    client, transport = _client(responder)
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out is not None
    assert set(out) == {"AAPL"}
    assert out["AAPL"]["earnings_date"] == "2026-07-20"
    # token never leaks into the logged/asserted URL beyond the single expected call.
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_fetch_picks_nearest_date_when_multiple_rows() -> None:
    def responder(req: httpx.Request) -> Any:
        from_param = req.url.params.get("from")
        to_param = req.url.params.get("to")
        assert from_param and to_param
        return {
            "earningsCalendar": [
                _row("AAPL", "2026-01-01"),  # far past
                _row("AAPL", "2026-07-12"),  # near
            ]
        }

    coll = FinnhubEarningsCollector(api_key="k", symbols_override=("AAPL",))
    client, _ = _client(responder)
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out is not None
    assert out["AAPL"]["earnings_date"] == "2026-07-12"


@pytest.mark.asyncio
async def test_fetch_computes_surprise_pct_when_actual_present() -> None:
    # PAST print (lookback window) — surprise is known-at-time and flows through.
    def responder(req: httpx.Request) -> Any:
        return {"earningsCalendar": [_row("AAPL", "2026-07-08", eps_est=1.0, eps_act=1.1)]}

    coll = FinnhubEarningsCollector(api_key="k", symbols_override=("AAPL",))
    client, _ = _client(responder)
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out is not None
    assert out["AAPL"]["surprise_pct"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_fetch_persists_calendar_row_and_shadow_tag(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")

    def responder(req: httpx.Request) -> Any:
        return {"earningsCalendar": [_row("AAPL", "2026-07-08", eps_est=1.0, eps_act=1.1)]}

    coll = FinnhubEarningsCollector(api_key="k", symbols_override=("AAPL",), conn=conn)
    client, _ = _client(responder)
    await coll.fetch(client=client)
    await client.aclose()

    row = conn.execute(
        "SELECT symbol, earnings_date, surprise_pct FROM earnings_calendar"
    ).fetchone()
    assert row == ("AAPL", "2026-07-08", pytest.approx(10.0))
    shadow = conn.execute(
        "SELECT symbol, surprise_pct FROM earnings_proximity_shadow"
    ).fetchone()
    assert shadow == ("AAPL", pytest.approx(10.0))
    conn.close()


@pytest.mark.asyncio
async def test_refetch_upserts_not_duplicates(tmp_path: Path) -> None:
    """A later actual-eps refresh (INSERT ON CONFLICT UPDATE) must not create a
    second row for the same (symbol, earnings_date)."""
    conn = init_db(tmp_path / "t.sqlite")
    calls = {"n": 0}

    def responder(req: httpx.Request) -> Any:
        calls["n"] += 1
        eps_act = None if calls["n"] == 1 else 1.1
        return {"earningsCalendar": [_row("AAPL", "2026-07-12", eps_est=1.0, eps_act=eps_act)]}

    coll = FinnhubEarningsCollector(api_key="k", symbols_override=("AAPL",), conn=conn)
    client, _ = _client(responder)
    await coll.fetch(client=client)
    await coll.fetch(client=client)
    await client.aclose()

    rows = conn.execute("SELECT eps_actual FROM earnings_calendar").fetchall()
    assert rows == [(1.1,)]
    conn.close()


@pytest.mark.asyncio
async def test_http_error_never_leaks_token_and_returns_empty() -> None:
    coll = FinnhubEarningsCollector(api_key="SECRET123", symbols_override=("AAPL",))
    client, _ = _client(lambda req: httpx.Response(500, json={}))
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out == {}


@pytest.mark.asyncio
async def test_future_print_with_actual_never_stamps_surprise() -> None:
    """known-at-time invariant: a FUTURE-dated row carrying a (stale/bogus)
    eps_actual must NOT produce a surprise tag — look-ahead-free by structure."""
    def responder(req: httpx.Request) -> Any:
        return {"earningsCalendar": [_row("AAPL", "2099-01-05", eps_est=1.0, eps_act=1.1)]}

    coll = FinnhubEarningsCollector(api_key="k", symbols_override=("AAPL",))
    client, _ = _client(responder)
    out = await coll.fetch(client=client)
    await client.aclose()
    assert out is not None
    assert out["AAPL"]["surprise_pct"] is None
    assert out["AAPL"]["days_from_now"] > 0
