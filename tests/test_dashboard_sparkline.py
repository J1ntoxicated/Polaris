"""Symbol sparkline (Jin 2026-06-25) — per-row recent-close mini history.

The dashboard rows (positions / recent_trades / ticker_stats) carry a small
``spark`` list of the symbol's most-recent N closes so the board can draw a tiny
inline trend graph next to each symbol. DISPLAY-ONLY — the series is read from
the ``bars`` cache (Yahoo primary), never feeds sizing/gating/exit. Missing bars
degrade to an empty list (graceful). These tests pin: presence + length cap,
empty-data graceful, downsample of an over-length history, and the per-instrument
freshest-interval pick (so a symbol with both daily + intraday bars sparks the
freshest series, not a mixed one).
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from polaris.scripts.dashboard.snapshot_q_common import _spark_series


def _bar(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    interval: str,
    ts: int,
    close: float,
) -> None:
    inst = f"{venue}:{symbol}"
    conn.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        " bar_interval, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0)",
        (inst, f"crypto:{symbol}", venue, symbol, interval, ts,
         close, close, close, close),
    )


def test_spark_series_present_and_ordered(memdb: sqlite3.Connection) -> None:
    # 5 hourly bars, all safely in the PAST (newest is ~1h ago).
    newest = int(time.time()) - 3600
    for i, px in enumerate([100.0, 101.0, 102.0, 103.0, 104.0]):
        _bar(memdb, venue="okx", symbol="BTC", interval="1H",
             ts=newest - (4 - i) * 3600, close=px)
    out = _spark_series(memdb, ["okx:BTC"], n=30)
    # newest LAST (chronological), exactly the 5 closes seeded.
    assert out["okx:BTC"] == [100.0, 101.0, 102.0, 103.0, 104.0]


def test_spark_series_empty_graceful(memdb: sqlite3.Connection) -> None:
    # No bars for the instrument → empty list, never a KeyError / crash.
    out = _spark_series(memdb, ["okx:NOPE"], n=30)
    assert out.get("okx:NOPE", []) == []


def test_spark_series_downsamples_overlong(memdb: sqlite3.Connection) -> None:
    # 200 hourly bars ending ~1h ago. The query keeps only the most-RECENT
    # window (_SPARK_FETCH_LIMIT) then downsamples to <= n — a sparkline shows
    # recent history, not the full life of the symbol. The newest anchor + the
    # length cap are what matter.
    newest = int(time.time()) - 3600
    for i in range(200):
        _bar(memdb, venue="okx", symbol="ETH", interval="1H",
             ts=newest - (199 - i) * 3600, close=float(i))
    out = _spark_series(memdb, ["okx:ETH"], n=30)
    series = out["okx:ETH"]
    assert len(series) <= 30
    assert series[-1] == 199.0           # newest anchor preserved
    assert series == sorted(series)      # monotone closes → ordered oldest→newest
    assert series[0] >= 140.0            # only the recent window, not bar 0


def test_spark_series_picks_freshest_interval(memdb: sqlite3.Connection) -> None:
    now = int(time.time())
    # Stale 1D bars (days old) + fresh 1H bars (minutes old) for the SAME symbol.
    for i in range(5):
        _bar(memdb, venue="alpaca", symbol="AAPL", interval="1D",
             ts=now - 86400 * (10 - i), close=10.0 + i)
    for i in range(5):
        _bar(memdb, venue="alpaca", symbol="AAPL", interval="1H",
             ts=now - 3600 * (5 - i), close=50.0 + i)
    out = _spark_series(memdb, ["alpaca:AAPL"], n=30)
    # The freshest interval (1H, newest ts) wins — series is the 50.x band.
    assert out["alpaca:AAPL"][-1] == pytest.approx(54.0)
    assert all(v >= 50.0 for v in out["alpaca:AAPL"])


def test_spark_series_excludes_future_dated(memdb: sqlite3.Connection) -> None:
    now = int(time.time())
    # A real recent bar + a +10h FUTURE-dated bar (the Capital AEST-naive bug).
    _bar(memdb, venue="capital", symbol="J225", interval="1H",
         ts=now - 3600, close=100.0)
    _bar(memdb, venue="capital", symbol="J225", interval="1H",
         ts=now + 10 * 3600, close=999.0)
    out = _spark_series(memdb, ["capital:J225"], n=30)
    # The future bar is excluded — newest visible close is the real one.
    assert 999.0 not in out["capital:J225"]
    assert out["capital:J225"][-1] == 100.0


def test_spark_series_multi_instrument_one_query(memdb: sqlite3.Connection) -> None:
    base = int(time.time()) - 50_000
    for i in range(3):
        _bar(memdb, venue="okx", symbol="BTC", interval="1H",
             ts=base + i * 3600, close=1.0 + i)
        _bar(memdb, venue="okx", symbol="SOL", interval="1H",
             ts=base + i * 3600, close=10.0 + i)
    out = _spark_series(memdb, ["okx:BTC", "okx:SOL"], n=30)
    assert out["okx:BTC"] == [1.0, 2.0, 3.0]
    assert out["okx:SOL"] == [10.0, 11.0, 12.0]


def test_spark_series_empty_instruments(memdb: sqlite3.Connection) -> None:
    # No instruments requested → empty dict, no query / crash.
    assert _spark_series(memdb, [], n=30) == {}


# --------------------------------------------------------------------------
# End-to-end: collect_snapshot attaches + serializes spark on the rows
# --------------------------------------------------------------------------


def test_collect_snapshot_attaches_spark_to_rows(tmp_path: object) -> None:
    """collect_snapshot embeds spark on positions / ticker / recent_trade rows
    and the dataclasses serialize (dataclasses.asdict) for the web snapshot."""
    import dataclasses
    from pathlib import Path

    from polaris.scripts.dashboard.snapshot import collect_snapshot
    from polaris.storage.schema import init_db

    db = Path(str(tmp_path)) / "polaris.sqlite"
    conn = init_db(db)
    try:
        # One closed BTC position (→ ticker stat + recent trade) and one open
        # SOL position, each with recent bars to spark.
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, "
            " underlying_group_id, strategy_id, entry_strategy_id, "
            " active_strategy_id, side, qty, status, opened_ts, closed_ts, "
            " swap_count) VALUES ('p1','okx','BTC','crypto:BTC','s','s','s',"
            " 'long',1.0,'closed',1000,2000,0)"
        )
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
            " size_usd, fill_price, ts_ms, order_id, contribution_id, base_qty, "
            " pnl_usd, is_close) VALUES ('p1c','okx','okx:BTC','s','sell',1000.0,"
            " 100.0,1500,'o1','p1',1.0,500.0,1)"
        )
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, "
            " underlying_group_id, strategy_id, entry_strategy_id, "
            " active_strategy_id, side, qty, status, opened_ts, swap_count) "
            " VALUES ('p2','okx','SOL','crypto:SOL','s','s','s','long',1.0,"
            " 'open',1000,0)"
        )
        newest = int(time.time()) - 3600
        for sym, base in (("BTC", 100.0), ("SOL", 10.0)):
            for i in range(5):
                _bar(conn, venue="okx", symbol=sym, interval="1H",
                     ts=newest - (4 - i) * 3600, close=base + i)
    finally:
        conn.close()

    snap = collect_snapshot(db)
    pos_by_sym = {p.symbol: p for p in snap.positions}
    assert pos_by_sym["SOL"].spark == [10.0, 11.0, 12.0, 13.0, 14.0]
    tick_by_sym = {t.symbol: t for t in snap.ticker_stats}
    assert tick_by_sym["BTC"].spark == [100.0, 101.0, 102.0, 103.0, 104.0]
    trade_btc = next(t for t in snap.recent_trades if t.symbol == "BTC")
    assert trade_btc.spark == [100.0, 101.0, 102.0, 103.0, 104.0]
    # Serializable for the web snapshot (server uses dataclasses.asdict).
    d = dataclasses.asdict(snap)
    assert d["positions"][0]["spark"] == pos_by_sym[
        snap.positions[0].symbol
    ].spark
