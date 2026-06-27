"""③ 1D depth backfill — OKX /history-candles paginated deep fetch (paced).

DEMO/PAPER only. flow_not_block: a one-shot, paced deep backfill that shares the
SAME ``_CANDLES_BUCKET`` (≤10 req/s) so it cannot recreate the #21 candles storm.
``/market/candles`` only serves the newest N with no paging — deep history needs
``/market/history-candles`` walked backward by ``before``/``after`` cursors.

Verifies:
1. ``fetch_okx_history_bars`` paginates: it keeps requesting older pages until a
   target depth (or an empty page) is reached, returning canonical Bars.
2. Each request goes through the shared candles bucket (paced, no burst).
3. A 4H interval is accepted (the deep canvas is interval-agnostic).
4. The retention ``bars`` window is wide enough to KEEP a multi-year backfill (the
   #1 blocker: a 400d prune would delete the backfill immediately).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from polaris.storage.retention import RETENTION_SPEC
from polaris.venues.okx.adapter import (
    OKX_HISTORY_CANDLES_PATH,
    fetch_okx_history_bars,
)


def _candle(ts_ms: int, close: float) -> list[str]:
    # OKX row: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    return [str(ts_ms), "100", "110", "95", str(close), "1000", "1000", "100000", "1"]


class _PagingTransport(httpx.MockTransport):
    """Serves descending history pages keyed by the ``after`` cursor.

    OKX history-candles returns NEWEST-first; ``after`` asks for rows OLDER than
    the cursor. We serve 3 pages of 2 bars each then an empty page (exhausted).
    """

    def __init__(self) -> None:
        self.calls = 0
        # ts in ms, 1D apart (86400_000), newest first overall.
        self._all = [
            _candle(500_000_000_000 - i * 86_400_000, 100.0 + i) for i in range(6)
        ]
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        after = request.url.params.get("after")
        if after is None:
            page = self._all[0:2]
        else:
            cursor = int(after)
            older = [r for r in self._all if int(r[0]) < cursor]
            page = older[0:2]
        return httpx.Response(200, json={"code": "0", "data": page})


@pytest.mark.asyncio
async def test_history_backfill_paginates() -> None:
    transport = _PagingTransport()
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    try:
        bars = await fetch_okx_history_bars(
            "BTC-USDT", bar_interval="1D", target_bars=6, client=client,
            page_pause_sec=0.0,
        )
    finally:
        await client.aclose()
    # 6 unique bars across 3 paged requests (2/page) + the exhausted 4th page.
    assert len(bars) == 6
    # Canonical + ascending (newest last) — the persist contract.
    assert all(b.bar_interval == "1D" and b.venue == "okx" for b in bars)
    ts_list = [b.ts for b in bars]
    assert ts_list == sorted(ts_list)
    assert transport.calls >= 3  # paginated, not a single newest-N pull


@pytest.mark.asyncio
async def test_history_backfill_paths_use_history_endpoint() -> None:
    """The deep fetch hits /history-candles (paginated), NOT /candles (newest-N)."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"code": "0", "data": []})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://us.okx.com"
    )
    try:
        await fetch_okx_history_bars(
            "ETH-USDT", bar_interval="4H", target_bars=10, client=client,
            page_pause_sec=0.0,
        )
    finally:
        await client.aclose()
    assert seen and all(p == OKX_HISTORY_CANDLES_PATH for p in seen)


@pytest.mark.asyncio
async def test_history_backfill_paced_through_shared_bucket(monkeypatch: Any) -> None:
    """Every page acquires from the shared _CANDLES_BUCKET (storm guard).

    ``AsyncTokenBucket`` uses ``__slots__`` (acquire is read-only), so we swap the
    whole module-level bucket for a counting wrapper that delegates to the real one.
    """
    import polaris.venues.okx.adapter as okx

    real_bucket = okx._CANDLES_BUCKET

    class _CountingBucket:
        def __init__(self) -> None:
            self.acquired = 0

        async def acquire(self, *, timeout: float | None = None) -> bool:
            self.acquired += 1
            return await real_bucket.acquire(timeout=timeout)

    spy = _CountingBucket()
    monkeypatch.setattr(okx, "_CANDLES_BUCKET", spy)
    transport = _PagingTransport()
    client = httpx.AsyncClient(transport=transport, base_url="https://us.okx.com")
    try:
        await fetch_okx_history_bars(
            "BTC-USDT", bar_interval="1D", target_bars=6, client=client,
            page_pause_sec=0.0,
        )
    finally:
        await client.aclose()
    assert spy.acquired >= 3  # one bucket acquire per page request


def test_retention_bars_window_allows_multiyear_backfill() -> None:
    """🚨 #1 blocker: the bars retention window for the DEEP DAILY canvas must be
    wide enough to KEEP a multi-year backfill (a 400d prune would delete it the
    next hygiene pass). NIT-A made bars retention PER-INTERVAL, so the 1D (and 4H)
    swing-canvas windows carry the deep window while the dense intraday streams
    prune short — assert the deep-canvas intervals specifically."""
    by_interval = {
        r.bar_interval: r for r in RETENTION_SPEC
        if r.table == "bars" and r.bar_interval
    }
    # >= ~3y so a deep daily/4H backfill (OKX caps ~2-3y of dailies) survives.
    assert by_interval["1D"].retain_sec >= 1100 * 86_400
    assert by_interval["4H"].retain_sec >= 1100 * 86_400
