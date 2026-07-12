"""Price-through shadow channel stats (maker_fill_sim R1 2026-07-12 debate).

DEMO/PAPER, display-only. Resolves ``price_through_shadow`` rows against the
already-ingested ``bars`` (SAME offline basis ``weekend_data.resolve_would_be_r``
uses) into a traded-through ratio + average price-improvement bps reading for
the SCOUT SHADOW promotion-gauge pattern (``polaris_graph._query_shadow_channels``).
Read-only — never gates/sizes/throttles a trade.
"""

from __future__ import annotations

import sqlite3

from tools.visualizer.price_through_channel import price_through_channel_stats


def _conn() -> sqlite3.Connection:
    from polaris.storage.schema import ALL_DDL

    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    return conn


def _insert_shadow_row(
    conn: sqlite3.Connection, *, event_id: str, venue: str, symbol: str,
    side: str, fill_px: float, touch_px: float, created_ts: int,
) -> None:
    conn.execute(
        "INSERT INTO price_through_shadow "
        "(event_id, run_id, strategy_id, venue, symbol, side, fill_px, "
        " touch_px, current_fee_bps, created_ts) "
        "VALUES (?, 'r', 's', ?, ?, ?, ?, ?, 10.0, ?)",
        (event_id, venue, symbol, side, fill_px, touch_px, created_ts),
    )


def _insert_bar(
    conn: sqlite3.Connection, *, symbol: str, venue: str, ts: int,
    high: float, low: float, close: float,
) -> None:
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        " bar_interval, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, 0.0)",
        (f"{venue}:{symbol}", symbol, venue, symbol, ts, close, high, low, close),
    )


def test_empty_table_returns_none_stats() -> None:
    conn = _conn()
    stats = price_through_channel_stats(conn)
    assert stats == {
        "n": 0, "traded_through_pct": None,
        "avg_price_improve_bps": None, "fresh_ts": None,
    }


def test_resolves_traded_through_ratio_and_avg_improve_bps() -> None:
    conn = _conn()
    _insert_shadow_row(
        conn, event_id="e1", venue="okx", symbol="BTC-USDT", side="buy",
        fill_px=100.0, touch_px=99.0, created_ts=1000,
    )
    # forward bar dips through the 99.0 touch — traded through.
    _insert_bar(
        conn, symbol="BTC-USDT", venue="okx", ts=1060,
        high=100.5, low=98.5, close=99.5,
    )
    _insert_shadow_row(
        conn, event_id="e2", venue="okx", symbol="ETH-USDT", side="buy",
        fill_px=50.0, touch_px=49.0, created_ts=1000,
    )
    # forward bar never comes down to 49.0 — unfilled.
    _insert_bar(
        conn, symbol="ETH-USDT", venue="okx", ts=1060,
        high=52.0, low=51.0, close=51.5,
    )
    stats = price_through_channel_stats(conn)
    assert stats["n"] == 2
    assert stats["traded_through_pct"] == 50.0
    assert stats["avg_price_improve_bps"] is not None
    assert stats["avg_price_improve_bps"] > 0.0
    assert stats["fresh_ts"] == 1000


def test_missing_table_degrades_to_none_stats() -> None:
    conn = sqlite3.connect(":memory:")  # no schema at all
    stats = price_through_channel_stats(conn)
    assert stats == {
        "n": None, "traded_through_pct": None,
        "avg_price_improve_bps": None, "fresh_ts": None,
    }
