"""Recency guard — stale-feed bars must not drive trading decisions.

Jin 2026-06-22 (coherence audit): the Alpaca feed went 98.7h stale. A bar
accessor that hands a 4-day-old "newest" candle to the strategy/regime/exit
canvas lets the bot make decisions on a dead price. ``read_recent_bars`` grows
a ``freshness_threshold_sec`` guard: when the newest stored bar is OLDER than
the threshold, the accessor returns ``[]`` (the trading path's existing
``if not bars / len(bars) < 30: continue`` then skips the symbol).

This is a DATA-HEALTH gate (stale/dead feed = no live price = cannot trade),
NOT a defensive throttle — flow_not_block is preserved: a FRESH feed is never
touched, and the guard AUTO-CLEARS the instant fresh data arrives. It applies to
ALL venues (OKX/Capital/Alpaca), not just the dead Alpaca feed.

DEMO/PAPER only. No sizing/gating/entry-block touched — the guard only refuses
to return a price that is provably dead.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from polaris.core.data.ingest import persist_bars
from polaris.core.data.schema import Bar
from polaris.scripts._production_bars import (
    BAR_STALENESS_MAX_SEC,
    read_recent_bars,
)
from polaris.storage.schema import init_db


def _bar(venue: str, symbol: str, ts: int, *, bar_interval: str = "1D") -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"equity:{symbol}",
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
        trade_count=5_000,
        vwap=100.4,
        bid_close=0.0,
        ask_close=0.0,
        spread_bps_close=0.0,
        source="alpaca_rest",
    )


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = init_db(tmp_path / "recency.sqlite")
    yield c
    c.close()


def test_fresh_newest_bar_is_returned(conn: sqlite3.Connection) -> None:
    """A symbol whose newest bar is within the freshness window trades."""
    now = int(time.time())
    # Daily bars: newest closed an hour ago — well within any threshold.
    for i in range(5):
        persist_bars(conn, [_bar("alpaca", "AAPL", now - 3600 - i * 86400)])
    bars = read_recent_bars(
        conn, venue="alpaca", symbol="AAPL", bar_interval="1D",
        freshness_threshold_sec=86400 * 5,
    )
    assert len(bars) == 5, "fresh feed must return its bars unchanged"
    assert bars[-1].ts == now - 3600  # newest last


def test_stale_newest_bar_is_skipped(conn: sqlite3.Connection) -> None:
    """The 98.7h-dead-feed case: newest bar older than threshold -> []."""
    now = int(time.time())
    # Newest bar is ~99h old — the live Alpaca incident.
    stale_anchor = now - int(99 * 3600)
    for i in range(5):
        persist_bars(conn, [_bar("alpaca", "CAST", stale_anchor - i * 86400)])
    bars = read_recent_bars(
        conn, venue="alpaca", symbol="CAST", bar_interval="1D",
        freshness_threshold_sec=12 * 3600,  # 12h freshness window
    )
    assert bars == [], "stale newest bar must skip the symbol (no dead-price decision)"


def test_guard_auto_clears_on_fresh_data(conn: sqlite3.Connection) -> None:
    """When a fresh bar lands, the same accessor immediately returns bars again.

    flow_not_block: the guard is not sticky — it is a pure function of the data,
    so a recovered feed trades on the very next read (no manual reset).
    """
    now = int(time.time())
    stale_anchor = now - int(99 * 3600)
    for i in range(3):
        persist_bars(conn, [_bar("alpaca", "GPUS", stale_anchor - i * 86400)])
    # Stale -> skipped.
    assert read_recent_bars(
        conn, venue="alpaca", symbol="GPUS", bar_interval="1D",
        freshness_threshold_sec=12 * 3600,
    ) == []
    # Feed recovers: a fresh bar arrives.
    persist_bars(conn, [_bar("alpaca", "GPUS", now - 600)])
    bars = read_recent_bars(
        conn, venue="alpaca", symbol="GPUS", bar_interval="1D",
        freshness_threshold_sec=12 * 3600,
    )
    assert bars, "guard must auto-clear once fresh data lands"
    assert bars[-1].ts == now - 600


def test_guard_applies_to_all_venues(conn: sqlite3.Connection) -> None:
    """The guard is venue-agnostic — a stale OKX feed is skipped too."""
    now = int(time.time())
    stale = now - int(99 * 3600)
    persist_bars(conn, [_bar("okx", "BTC-USDT", stale, bar_interval="1m")])
    bars = read_recent_bars(
        conn, venue="okx", symbol="BTC-USDT", bar_interval="1m",
        freshness_threshold_sec=600,
    )
    assert bars == []


def test_no_threshold_means_no_recency_guard(conn: sqlite3.Connection) -> None:
    """Back-compat: callers that pass no threshold get the legacy behaviour.

    The FUTURE-dated ts<=now+slack guard still applies; only the new
    stale-floor is opt-in so existing call sites are byte-identical until they
    pass a threshold.
    """
    now = int(time.time())
    stale = now - int(99 * 3600)
    persist_bars(conn, [_bar("alpaca", "CRVO", stale)])
    bars = read_recent_bars(conn, venue="alpaca", symbol="CRVO", bar_interval="1D")
    assert len(bars) == 1, "no threshold -> legacy behaviour (stale bar still returned)"


def test_staleness_default_constant_is_sane() -> None:
    """The shipped default threshold is a real freshness window, not infinity."""
    assert BAR_STALENESS_MAX_SEC > 0
    # Must be wide enough that a normal daily-bar cadence (1/day) is never
    # mis-flagged, but far tighter than the 98.7h dead-feed incident.
    assert BAR_STALENESS_MAX_SEC <= 48 * 3600
