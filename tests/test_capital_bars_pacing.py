"""Capital bars 429-storm fix — bars-fetch PACING + priority (DEMO/PAPER only).

Root cause: the off-tick static-ground full-universe walk
(``_static_ground.ingest_static_ground_bars``) fanned out ONE
``/prices/{epic}`` GET per (venue, symbol, resolution) with NO client-side
pacing on the Capital exchange-fallback path — the exotic-FX/commodity tail
(SEKMXN / HKDTRY / AUDUSD_ZERO / ...) that Yahoo has no series for hammered
Capital's demo REST far above its ~10 req/s per-account ceiling, 429-storming
GOLD/US100/UK100 bar-advances for the strategies that actually trade them.

Fix mirrors the existing OKX ``_CANDLES_BUCKET`` / Alpaca ``_data_bucket``
wait-not-drop pattern:
  (a) a shared ``AsyncTokenBucket`` paces every ``fetch_capital_bars`` GET;
  (b) the off-tick static-ground walk acquires with ``wait_for_token=True``
      (a real wait); the live 5s tick path keeps the bounded soft-cap acquire
      (byte-identical — never stalls the tick);
  (c) the static-ground walk schedules the SSOT-derived tradeable Capital set
      (what registered strategies actually trade) BEFORE the exotic tail, so a
      saturated bucket/timeout degrades the exotic tail first, never GOLD/
      US100/UK100/etc.

flow_not_block / aggressive bias preserved: pacing WAITS for a token, it never
drops or blocks a bar; no entry/exit/sizing path is touched (CapitalAdapter's
order lifecycle — open/close/list/confirm — goes through ``CapitalSession.
request``, a completely separate path from this bucket).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from polaris.venues.capital.adapter import (
    _BARS_BUCKET,
    BARS_PER_SEC,
    BARS_RATE,
    CAPITAL_BASE_DEMO,
    fetch_capital_bars,
)


class _Responder:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.handler: Any = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        return self.handler(req)


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responder: _Responder) -> None:
        self.responder = responder

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        out = self.responder(request)
        return out if isinstance(out, httpx.Response) else httpx.Response(200, json=out)


def _prices_ok() -> dict[str, Any]:
    return {"prices": []}


@pytest.fixture(autouse=True)
def _reset_bars_bucket() -> None:
    """Give every test a full bucket (module-level singleton, tests share it)."""
    _BARS_BUCKET._tokens = _BARS_BUCKET._capacity  # noqa: SLF001 — test reset only


# ---------------------------------------------------------------------------
# (a) pacing: every bars GET acquires a token first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bars_fetch_paces_through_token_bucket() -> None:
    """A bars GET acquires a token BEFORE hitting the network (pre-throttle)."""
    events: list[str] = []

    class _StubBucket:
        async def acquire(self, *, timeout: float | None = None) -> bool:
            events.append("acquire")
            return True

    def _h(req: httpx.Request) -> httpx.Response:
        events.append("get")
        return httpx.Response(200, json=_prices_ok())

    responder = _Responder()
    responder.handler = _h
    client = httpx.AsyncClient(transport=_MockTransport(responder), base_url=CAPITAL_BASE_DEMO)
    with patch("polaris.venues.capital.adapter._BARS_BUCKET", _StubBucket()):
        try:
            bars = await fetch_capital_bars(
                "GOLD", cst="c", security_token="s", resolution="DAY", client=client,
            )
        finally:
            await client.aclose()
    assert bars == []
    assert events == ["acquire", "get"]  # paced once, before the network GET


@pytest.mark.asyncio
async def test_concurrent_bars_fetch_never_exceeds_rate() -> None:
    """A fan-out of many symbols cannot burst past the shared bucket's rate.

    Mirrors the live incident shape: the static-ground walk fires many
    concurrent per-symbol GETs. Only ``BARS_RATE`` may land within one
    ``BARS_PER_SEC`` window; the rest wait (pacing, not rejection/drop).
    """
    import asyncio
    import time

    responder = _Responder()
    responder.handler = lambda req: httpx.Response(200, json=_prices_ok())
    client = httpx.AsyncClient(transport=_MockTransport(responder), base_url=CAPITAL_BASE_DEMO)
    n_symbols = BARS_RATE * 3  # 3x capacity — some MUST wait for refill
    start = time.monotonic()
    try:
        await asyncio.gather(
            *(
                fetch_capital_bars(
                    f"SYM{i}", cst="c", security_token="s", resolution="DAY", client=client,
                    wait_for_token=True,
                )
                for i in range(n_symbols)
            )
        )
    finally:
        await client.aclose()
    elapsed = time.monotonic() - start
    # 3x capacity through an 8/1.0s bucket must take at least ~2 refill windows.
    assert elapsed >= (2.0 / BARS_PER_SEC) * 0.5  # generous floor, avoids flakiness
    assert len(responder.calls) == n_symbols  # every symbol still got fetched


# ---------------------------------------------------------------------------
# (b) 429 retry/backoff + wait_for_token threading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bars_429_then_success_retries() -> None:
    """A transient 429 is retried with backoff, then succeeds (bar not dropped)."""
    state = {"n": 0}

    def _h(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=_prices_ok())

    responder = _Responder()
    responder.handler = _h
    client = httpx.AsyncClient(transport=_MockTransport(responder), base_url=CAPITAL_BASE_DEMO)
    with patch("polaris.venues.capital.adapter._async_sleep", new=AsyncMock()):
        try:
            bars = await fetch_capital_bars(
                "GOLD", cst="c", security_token="s", resolution="DAY", client=client,
            )
        finally:
            await client.aclose()
    assert state["n"] == 3  # retried past the two 429s
    assert bars == []


@pytest.mark.asyncio
async def test_bars_429_exhausted_raises_httpstatuserror() -> None:
    """Exhausting the retry budget raises (caller degrades to [], never drops silently)."""
    responder = _Responder()
    responder.handler = lambda req: httpx.Response(429, headers={"Retry-After": "0"})
    client = httpx.AsyncClient(transport=_MockTransport(responder), base_url=CAPITAL_BASE_DEMO)
    with (
        patch("polaris.venues.capital.adapter._async_sleep", new=AsyncMock()),
        pytest.raises(httpx.HTTPStatusError),
    ):
        try:
            await fetch_capital_bars(
                "GOLD", cst="c", security_token="s", resolution="DAY", client=client,
            )
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_wait_for_token_false_uses_bounded_timeout() -> None:
    """Live-tick default (``wait_for_token=False``) acquires with a bounded timeout."""
    from polaris.venues.capital.adapter import BARS_ACQUIRE_TIMEOUT_SEC

    seen: dict[str, Any] = {}

    class _StubBucket:
        async def acquire(self, *, timeout: float | None = None) -> bool:
            seen["timeout"] = timeout
            return True

    responder = _Responder()
    responder.handler = lambda req: httpx.Response(200, json=_prices_ok())
    client = httpx.AsyncClient(transport=_MockTransport(responder), base_url=CAPITAL_BASE_DEMO)
    with patch("polaris.venues.capital.adapter._BARS_BUCKET", _StubBucket()):
        try:
            await fetch_capital_bars(
                "GOLD", cst="c", security_token="s", resolution="DAY", client=client,
                wait_for_token=False,
            )
        finally:
            await client.aclose()
    assert seen["timeout"] == BARS_ACQUIRE_TIMEOUT_SEC


@pytest.mark.asyncio
async def test_wait_for_token_true_waits_unbounded() -> None:
    """Off-tick static-ground pass (``wait_for_token=True``) acquires with timeout=None."""
    seen: dict[str, Any] = {}

    class _StubBucket:
        async def acquire(self, *, timeout: float | None = None) -> bool:
            seen["timeout"] = timeout
            return True

    responder = _Responder()
    responder.handler = lambda req: httpx.Response(200, json=_prices_ok())
    client = httpx.AsyncClient(transport=_MockTransport(responder), base_url=CAPITAL_BASE_DEMO)
    with patch("polaris.venues.capital.adapter._BARS_BUCKET", _StubBucket()):
        try:
            await fetch_capital_bars(
                "GOLD", cst="c", security_token="s", resolution="DAY", client=client,
                wait_for_token=True,
            )
        finally:
            await client.aclose()
    assert seen["timeout"] is None


@pytest.mark.asyncio
async def test_trade_lifecycle_never_touches_bars_bucket() -> None:
    """CapitalAdapter order lifecycle (open/close/list/confirm) bypasses the bars bucket.

    Aggressive bias: a saturated bars bucket must never slow/stall an order —
    the trade path (``CapitalSession.request``) does not import or call
    ``_BARS_BUCKET`` at all (a structural guarantee, not just an empty count).
    """
    import inspect

    from polaris.venues.capital import adapter as capital_adapter_mod

    src = inspect.getsource(capital_adapter_mod.CapitalAdapter)
    assert "_BARS_BUCKET" not in src
