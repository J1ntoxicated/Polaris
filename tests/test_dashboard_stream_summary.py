"""Tests for dashboard stage-2 BACKEND — per-stream summary in the snapshot.

Read-only display layer (DEMO/PAPER, virtual funds). Seeds an in-memory/temp
SQLite DB with fills + positions across okx / capital / alpaca and asserts:

- exactly 3 ``StreamSummary`` lanes (one per registered stream, even a venue
  with zero activity yields a zeroed lane so all 3 lanes always render);
- per-stream sums reconcile to the existing global totals (``net_pnl_usd``,
  ``open_positions_n``) so the dashboard never lies;
- stream_id / label / color / venue / product_class are sourced from the
  streams SSOT (``polaris.core.streams.config``), not a second hardcoded map.

These tests touch only the snapshot/visualizer read path — no trading
behavior, sizing, gating, or venue calls.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.streams.config import STREAMS, VENUE_TO_STREAM, StreamConfig
from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_models import StreamSummary
from polaris.scripts.dashboard.snapshot_queries import (
    _daily_realised_pnl,
    _now_s,
    _per_stream_summary,
)
from polaris.storage.schema import init_db

# str-keyed view of the SSOT (StreamSummary.stream_id is a plain str).
_CFG_BY_ID: dict[str, StreamConfig] = {
    cfg.stream_id: cfg for cfg in STREAMS.values()
}


def _seed(conn: sqlite3.Connection) -> None:
    """Seed fills + open positions across okx / capital (alpaca left empty)."""
    now_ms = _now_s() * 1000

    # fills: (fill_id, venue, instrument_id, strategy_id, side, size_usd,
    #         fill_price, fee_usd, slippage_bps, ts_ms, order_id,
    #         contribution_id, pnl_usd, is_close, base_qty, quote_qty, state)
    fills = [
        # okx — one open + one closing fill (+50 pnl, -2 fee)
        ("f1", "okx", "okx:BTC-USDT", "tsmom", "buy", 1000.0, 100.0, 1.0, 0.0,
         now_ms - 5000, "o1", None, 0.0, 0, 10.0, 1000.0, "filled"),
        ("f2", "okx", "okx:BTC-USDT", "tsmom", "sell", 1050.0, 105.0, 1.0, 0.0,
         now_ms - 4000, "o2", None, 50.0, 1, 10.0, 1050.0, "filled"),
        # capital — one open + one closing fill (-20 pnl, -3 fee)
        ("f3", "capital", "capital:XAUUSD", "xau_indices_trend", "buy", 2000.0,
         1900.0, 1.5, 0.0, now_ms - 3000, "o3", None, 0.0, 0, 1.0, 2000.0,
         "filled"),
        ("f4", "capital", "capital:XAUUSD", "xau_indices_trend", "sell", 1980.0,
         1880.0, 1.5, 0.0, now_ms - 2000, "o4", None, -20.0, 1, 1.0, 1980.0,
         "filled"),
    ]
    conn.executemany(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        fills,
    )

    # positions: okx 1 open, capital 2 open (drift collapses on logical key but
    # distinct symbols stay separate), alpaca 0.
    now_s = _now_s()
    positions = [
        # (position_id, venue, symbol, underlying_group_id, product_class,
        #  stream_id, strategy_id, entry_strategy_id, active_strategy_id,
        #  side, qty, status, opened_ts, closed_ts, swap_count)
        ("p1", "okx", "ETH-USDT", "", "spot", "A_okx_crypto", "tsmom",
         "tsmom", "tsmom", "long", 5.0, "open", now_s - 100, None, 0),
        ("p2", "capital", "EURUSD", "", "cfd", "B_capital_cfd",
         "fx_breakout_basket", "fx_breakout_basket", "fx_breakout_basket",
         "long", 1000.0, "open", now_s - 80, None, 0),
        ("p3", "capital", "GBPUSD", "", "cfd", "B_capital_cfd",
         "fx_breakout_basket", "fx_breakout_basket", "fx_breakout_basket",
         "long", 800.0, "open", now_s - 60, None, 0),
    ]
    conn.executemany(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, product_class, stream_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, closed_ts, swap_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        positions,
    )

    # bars: last close per instrument so open positions get a mark (so
    # per-stream uPnL is non-trivial; reconciliation holds regardless).
    bars = [
        # (instrument_id, venue, symbol, close)
        ("okx:ETH-USDT", "okx", "ETH-USDT", 2010.0),
        ("capital:EURUSD", "capital", "EURUSD", 1.11),
        ("capital:GBPUSD", "capital", "GBPUSD", 1.29),
    ]
    conn.executemany(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        "bar_interval, ts, open, high, low, close, volume) "
        "VALUES (?, '', ?, ?, '1m', ?, 0.0, 0.0, 0.0, ?, 0.0)",
        [(b[0], b[1], b[2], now_s, b[3]) for b in bars],
    )
    conn.commit()


def _seeded_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    _seed(conn)
    return conn


def test_three_lanes_always_present(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    assert len(streams) == 3
    assert all(isinstance(s, StreamSummary) for s in streams)
    ids = {s.stream_id for s in streams}
    assert ids == set(STREAMS.keys())


def test_empty_venue_yields_zeroed_lane(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    alpaca = next(s for s in streams if s.stream_id == "C_alpaca_equity")
    assert alpaca.venue == "alpaca"
    assert alpaca.open_positions_n == 0
    assert alpaca.daily_trades == 0
    assert alpaca.net_pnl_usd == 0.0
    assert alpaca.upnl_usd == 0.0
    assert alpaca.exposed_usd == 0.0


def test_label_color_from_ssot(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    for s in streams:
        cfg = _CFG_BY_ID[s.stream_id]
        assert s.venue == cfg.venue
        assert s.product_class == cfg.product_class
        # venue resolves back to this stream via the SSOT reverse index.
        assert VENUE_TO_STREAM[s.venue] == s.stream_id
        # label + color are non-empty deterministic display attributes.
        assert s.label
        assert s.color
    # distinct color per lane (so the 3 lanes are visually separable).
    colors = [s.color for s in streams]
    assert len(set(colors)) == len(colors)


def test_per_stream_pnl_breakdown(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    by_id = {s.stream_id: s for s in streams}
    okx = by_id["A_okx_crypto"]
    cap = by_id["B_capital_cfd"]
    # okx net realised = +50 pnl - (1 + 1) fees = +48; 1 closed trade.
    assert okx.net_pnl_usd == 48.0
    assert okx.daily_trades == 1
    assert okx.open_positions_n == 1
    # capital net realised = -20 pnl - (1.5 + 1.5) fees = -23; 1 closed trade.
    assert cap.net_pnl_usd == -23.0
    assert cap.daily_trades == 1
    assert cap.open_positions_n == 2


def test_reconciliation_invariant_pnl_and_open_n(tmp_path: Path) -> None:
    """SUM(per-stream) must equal the existing global totals (no lying)."""
    conn = _seeded_db(tmp_path)
    try:
        now_s = _now_s()
        streams = _per_stream_summary(conn, now_s=now_s)
        global_pnl, global_trades = _daily_realised_pnl(conn, now_s=now_s)
        # global open_positions_n via the snapshot collector below.
    finally:
        conn.close()

    sum_pnl = sum(s.net_pnl_usd for s in streams)
    sum_trades = sum(s.daily_trades for s in streams)
    assert round(sum_pnl, 6) == round(global_pnl, 6)
    assert sum_trades == global_trades


def test_collect_snapshot_populates_streams(tmp_path: Path) -> None:
    """End-to-end: collect_snapshot exposes streams whose sums reconcile to the
    global snapshot totals (open_positions_n, daily_pnl_usd)."""
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    _seed(conn)
    conn.close()

    snap = collect_snapshot(db_path)
    assert len(snap.streams) == 3
    sum_open = sum(s.open_positions_n for s in snap.streams)
    sum_pnl = sum(s.net_pnl_usd for s in snap.streams)
    assert sum_open == snap.open_positions_n
    assert round(sum_pnl, 6) == round(snap.daily_pnl_usd, 6)
    # uPnL reconciliation: per-stream uPnL sums to the global uPnL total.
    sum_upnl = sum(s.upnl_usd for s in snap.streams)
    assert round(sum_upnl, 6) == round(snap.upnl_total, 6)
    # exposed reconciliation: per-stream exposed sums to the global exposed.
    sum_exposed = sum(s.exposed_usd for s in snap.streams)
    assert round(sum_exposed, 6) == round(snap.exposed_usd, 6)
