"""Alpaca bar-FRESHNESS fix — RTH 0-trade incident (2026-06-22).

GROUNDED root cause (live-probed): the bot's ``fetch_bars`` sent NO ``feed`` and
NO ``sort`` param. Alpaca defaulted to a DELAYED entitlement and ASCENDING order,
so a 1m window wider than ``limit`` (240) returned the OLDEST 240 bars → the
NEWEST returned 1m bar was 1-2.6h stale during US RTH (TSLA 2.3h / AMD 1.0h
verified live). The (correct) recency guard then rejected every liquid symbol →
0 signals → 0 trades.

FIX: ``fetch_bars`` requests ``feed='iex'`` (real-time IEX prints; this account's
SIP/default is 15-min delayed) + ``sort='desc'`` (so ``limit`` keeps the NEWEST
bars). ``fetch_alpaca_bars`` re-sorts ascending to keep the canonical newest-LAST
contract. flow_not_block: gets FRESH data flowing, never trades stale.

DEMO/PAPER only. No sizing/gating touched.
"""

from __future__ import annotations

from typing import Any

import pytest

from polaris.scripts._production_bars import fetch_alpaca_bars


class _CapturingAdapter:
    """Records the params ``fetch_alpaca_bars`` forwards to ``fetch_bars`` and
    returns rows in DESCENDING (newest-first) order — what ``sort='desc'`` yields.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[dict[str, Any]] = []

    async def fetch_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Min",
        limit: int = 300,
        start: str | None = None,
        feed: str = "iex",
        sort: str = "desc",
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "limit": limit,
                "start": start,
                "feed": feed,
                "sort": sort,
            }
        )
        return self._rows


def _desc_rows() -> list[dict[str, Any]]:
    """Three 1m bars NEWEST-FIRST (descending), as ``sort='desc'`` returns."""
    return [
        {"t": "2024-01-02T05:02:00Z", "o": 102.0, "h": 103.0, "l": 101.0,
         "c": 102.5, "v": 30, "n": 3, "vw": 102.4},
        {"t": "2024-01-02T05:01:00Z", "o": 101.0, "h": 102.0, "l": 100.0,
         "c": 101.5, "v": 20, "n": 2, "vw": 101.4},
        {"t": "2024-01-02T05:00:00Z", "o": 100.0, "h": 101.0, "l": 99.0,
         "c": 100.5, "v": 10, "n": 1, "vw": 100.4},
    ]


@pytest.mark.asyncio
async def test_fetch_bars_requests_iex_feed() -> None:
    """The feed MUST be IEX — this account's default/SIP is 15-min delayed, so a
    delayed feed is the stale-canvas bug. IEX is real-time."""
    adapter = _CapturingAdapter(_desc_rows())
    await fetch_alpaca_bars(adapter, "AMD", bar_interval="1m", limit=240)
    assert adapter.calls[0]["feed"] == "iex"


@pytest.mark.asyncio
async def test_fetch_bars_requests_descending_sort() -> None:
    """sort='desc' makes ``limit`` keep the NEWEST bars (the asc default kept the
    OLDEST 240 of a window wider than limit → provably stale)."""
    adapter = _CapturingAdapter(_desc_rows())
    await fetch_alpaca_bars(adapter, "AMD", bar_interval="1m", limit=240)
    assert adapter.calls[0]["sort"] == "desc"


@pytest.mark.asyncio
async def test_fetch_bars_reorders_desc_input_to_newest_last() -> None:
    """Even when the venue returns NEWEST-FIRST (sort=desc), the canonical output
    is newest-LAST (ascending ts) so downstream indicators see the right order."""
    adapter = _CapturingAdapter(_desc_rows())
    bars = await fetch_alpaca_bars(adapter, "AMD", bar_interval="1m", limit=240)
    ts_seq = [b.ts for b in bars]
    assert ts_seq == sorted(ts_seq), "canonical bars must be newest-LAST"
    assert bars[-1].close == 102.5, "the newest bar must be last"
    assert bars[0].close == 100.5, "the oldest bar must be first"
