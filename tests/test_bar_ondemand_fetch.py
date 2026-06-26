"""On-demand live fetch — a stale feed FETCHES instead of silently skipping.

Jin 2026-06-27 ("데이터 proactive"): the recency guard returns ``[]`` when the
newest stored bar is stale, and the trading-path caller then SKIPS the symbol.
Across the live venues this skipped a lot (OKX ~179 stale-skips, Capital ~59,
Alpaca whenever the daily bar aged out) — so a fresh market with a momentarily
stale store evaluated 0 strategies. Wrong default: "no data → wait/skip". Right
default: "no data → GO GET IT".

``read_recent_bars_ondemand`` keeps the recency guard but, on a stale/empty
read, REFETCHES live via ``fetch_bars_one`` (Yahoo PRIMARY), persists the fresh
bars, and re-reads. Guard rails (storm / OKX-429 protection): a per-(venue,
symbol, interval) cooldown (``POLARIS_ONDEMAND_FETCH_COOLDOWN_SEC``, default 60s)
dedups the refetch; a failed/empty refetch degrades gracefully (returns ``[]`` —
the same skip as before, never a crash). flow_not_block: a FRESH read never
fetches (byte-identical to ``read_recent_bars``); the refetch only WIDENS data.

DEMO/PAPER only. No entry/size/exit touched — data layer only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

import polaris.scripts._production_bars as pbars
from polaris.core.data.ingest import persist_bars
from polaris.core.data.schema import Bar
from polaris.scripts._production_bars import (
    ONDEMAND_FETCH_COOLDOWN_SEC,
    read_recent_bars_ondemand,
    should_fetch_ondemand,
)
from polaris.storage.schema import init_db


def _bar(venue: str, symbol: str, ts: int, *, bar_interval: str = "1m") -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"crypto:{symbol}",
        venue=venue,
        symbol=symbol,
        bar_interval=bar_interval,
        ts=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1_000_000.0,
        notional_usd=100_500_000.0,
        trade_count=0,
        vwap=0.0,
        bid_close=0.0,
        ask_close=0.0,
        spread_bps_close=0.0,
        source="okx",
    )


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = init_db(tmp_path / "ondemand.sqlite")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _reset_ondemand_cooldown() -> Any:
    pbars._ONDEMAND_LAST_MONO.clear()
    yield
    pbars._ONDEMAND_LAST_MONO.clear()


@pytest.mark.asyncio
async def test_fresh_read_does_not_fetch(conn: sqlite3.Connection) -> None:
    """A fresh feed returns its bars with NO refetch (byte-identical path)."""
    now = int(time.time())
    for i in range(40):
        persist_bars(conn, [_bar("okx", "BTC-USDT", now - 60 - i * 60)])
    calls: list[tuple[str, str]] = []

    async def _spy_fetch(venue: str, symbol: str, *a: Any, **k: Any) -> list[Bar]:
        calls.append((venue, symbol))
        return []

    bars = await read_recent_bars_ondemand(
        conn, venue="okx", symbol="BTC-USDT", asset_class="crypto",
        bar_interval="1m", freshness_threshold_sec=30 * 60,
        fetch_fn=_spy_fetch,
    )
    assert len(bars) == 40, "fresh read returns stored bars unchanged"
    assert calls == [], "fresh read must NOT trigger an on-demand fetch"


@pytest.mark.asyncio
async def test_stale_read_refetches_persists_and_returns_fresh(
    conn: sqlite3.Connection,
) -> None:
    """Stale store → live refetch → persist → re-read returns the fresh bars."""
    now = int(time.time())
    # Stored bars are ~99h old (dead-feed) → recency guard would skip.
    stale = now - int(99 * 3600)
    for i in range(5):
        persist_bars(conn, [_bar("okx", "ETH-USDT", stale - i * 60)])

    fresh_bars = [_bar("okx", "ETH-USDT", now - 30 - i * 60) for i in range(35)]

    async def _fetch(venue: str, symbol: str, *a: Any, **k: Any) -> list[Bar]:
        return list(reversed(fresh_bars))  # newest-last contract preserved on sort

    bars = await read_recent_bars_ondemand(
        conn, venue="okx", symbol="ETH-USDT", asset_class="crypto",
        bar_interval="1m", freshness_threshold_sec=30 * 60,
        fetch_fn=_fetch,
    )
    assert bars, "on-demand fetch must populate fresh bars instead of skipping"
    assert bars[-1].ts == now - 30, "newest fresh bar is returned (newest last)"
    # The fresh bars were persisted (durable, not just returned).
    stored = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE symbol = 'ETH-USDT' AND ts >= ?",
        (now - 30 - 35 * 60,),
    ).fetchone()
    assert int(stored[0]) >= 35


@pytest.mark.asyncio
async def test_cooldown_dedups_refetch(conn: sqlite3.Connection) -> None:
    """A second stale read within the cooldown must NOT refetch (OKX-429 guard)."""
    now = int(time.time())
    stale = now - int(99 * 3600)
    persist_bars(conn, [_bar("okx", "SOL-USDT", stale)])
    calls: list[int] = []

    async def _fetch(venue: str, symbol: str, *a: Any, **k: Any) -> list[Bar]:
        calls.append(1)
        return []  # empty → no persist; the point is the call count

    common = dict(
        venue="okx", symbol="SOL-USDT", asset_class="crypto",
        bar_interval="1m", freshness_threshold_sec=30 * 60, fetch_fn=_fetch,
    )
    await read_recent_bars_ondemand(conn, now_mono=1000.0, **common)
    await read_recent_bars_ondemand(conn, now_mono=1000.0 + 1.0, **common)
    assert sum(calls) == 1, "cooldown must dedup the refetch within the window"
    # After the cooldown elapses, a refetch is allowed again.
    await read_recent_bars_ondemand(
        conn, now_mono=1000.0 + ONDEMAND_FETCH_COOLDOWN_SEC + 1.0, **common
    )
    assert sum(calls) == 2, "refetch resumes once the cooldown elapses"


@pytest.mark.asyncio
async def test_failed_refetch_degrades_to_empty(conn: sqlite3.Connection) -> None:
    """A refetch that returns nothing degrades to [] — same skip, never a crash."""
    now = int(time.time())
    stale = now - int(99 * 3600)
    persist_bars(conn, [_bar("okx", "XRP-USDT", stale)])

    async def _fetch(venue: str, symbol: str, *a: Any, **k: Any) -> list[Bar]:
        return []

    bars = await read_recent_bars_ondemand(
        conn, venue="okx", symbol="XRP-USDT", asset_class="crypto",
        bar_interval="1m", freshness_threshold_sec=30 * 60, fetch_fn=_fetch,
    )
    assert bars == [], "no fresh data available → graceful skip (degrade-never-crash)"


@pytest.mark.asyncio
async def test_fetch_exception_degrades_to_empty(conn: sqlite3.Connection) -> None:
    """A refetch that RAISES is swallowed → [] (one bad symbol never crashes tick)."""
    now = int(time.time())
    stale = now - int(99 * 3600)
    persist_bars(conn, [_bar("capital", "J225", stale, bar_interval="1H")])

    async def _boom(venue: str, symbol: str, *a: Any, **k: Any) -> list[Bar]:
        raise RuntimeError("yahoo blew up")

    bars = await read_recent_bars_ondemand(
        conn, venue="capital", symbol="J225", asset_class="index",
        bar_interval="1H", freshness_threshold_sec=6 * 3600, fetch_fn=_boom,
    )
    assert bars == [], "a fetch exception degrades to skip, never propagates"


def test_should_fetch_ondemand_cooldown_window() -> None:
    """The cooldown predicate spaces per-(venue, symbol, interval) refetches."""
    assert should_fetch_ondemand("okx", "BTC-USDT", "1m", 100.0) is True
    # Same key inside the window → blocked.
    assert should_fetch_ondemand("okx", "BTC-USDT", "1m", 110.0) is False
    # A DIFFERENT interval for the same symbol is independent.
    assert should_fetch_ondemand("okx", "BTC-USDT", "1H", 110.0) is True
    # After the cooldown → allowed again.
    assert should_fetch_ondemand(
        "okx", "BTC-USDT", "1m", 100.0 + ONDEMAND_FETCH_COOLDOWN_SEC + 1.0
    ) is True


def test_ondemand_cooldown_constant_sane() -> None:
    """Default cooldown is a real positive window (storm guard), env-tunable."""
    assert ONDEMAND_FETCH_COOLDOWN_SEC > 0
