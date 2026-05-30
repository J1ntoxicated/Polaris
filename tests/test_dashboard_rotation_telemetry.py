"""Tests for dashboard follow-up #12 — per-stream open/closed split + rotation
and session-forced-exit telemetry surfaced into the read-only snapshot.

DEMO/PAPER, virtual funds. READ-ONLY display layer: these tests seed a temp
SQLite DB and assert the snapshot query layer surfaces

  - per-stream ``closed_n`` (closed-fill count) + a recent-closed list, distinct
    from the already-present ``open_positions_n``;
  - rotation telemetry (count + last victim / E$_new / E$_held / margin / cost)
    read from the ``loop_rotation_events`` table the wire persists;
  - session-forced-exit counter read from ``loop_session_exit_events``;
  - graceful zero when no rotation / forced-exit / closed rows exist.

No trading behavior, sizing, gating, or venue calls are touched — only the
snapshot read path + a best-effort telemetry table the rotation/forced-exit
wires append to.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.streams.config import STREAMS
from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_queries import (
    _now_s,
    _per_stream_summary,
)
from polaris.scripts.dashboard.snapshot_sections import _rotation_telemetry
from polaris.storage.schema import init_db


def _seed_fills_positions(conn: sqlite3.Connection) -> None:
    """okx: 1 open + 2 closed ; capital: 1 open + 1 closed ; alpaca: empty."""
    now_ms = _now_s() * 1000
    now_s = _now_s()
    fills = [
        # okx — open p, then two close fills (2 closed trades)
        ("rf1", "okx", "okx:BTC-USDT", "tsmom", "buy", 1000.0, 100.0, 1.0, 0.0,
         now_ms - 9000, "o1", "rp_open_okx", 0.0, 0, 10.0, 1000.0, "filled"),
        ("rf2", "okx", "okx:BTC-USDT", "tsmom", "sell", 1050.0, 105.0, 1.0, 0.0,
         now_ms - 8000, "o2", "rp_open_okx", 50.0, 1, 10.0, 1050.0, "filled"),
        ("rf3", "okx", "okx:SOL-USDT", "tsmom", "sell", 500.0, 50.0, 0.5, 0.0,
         now_ms - 7000, "o3", "rp_sol", -10.0, 1, 10.0, 500.0, "filled"),
        # capital — one closing fill (1 closed trade)
        ("rf4", "capital", "capital:XAUUSD", "xau_indices_trend", "sell", 2000.0,
         1900.0, 1.5, 0.0, now_ms - 6000, "o4", "rp_xau", 20.0, 1, 1.0, 2000.0,
         "filled"),
    ]
    conn.executemany(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        fills,
    )
    positions = [
        ("rp1", "okx", "ETH-USDT", "", "spot", "A_okx_crypto", "tsmom",
         "tsmom", "tsmom", "long", 5.0, "open", now_s - 100, None, 0),
        ("rp2", "capital", "EURUSD", "", "cfd", "B_capital_cfd",
         "fx_breakout_basket", "fx_breakout_basket", "fx_breakout_basket",
         "long", 1000.0, "open", now_s - 80, None, 0),
    ]
    conn.executemany(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, product_class, stream_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, closed_ts, swap_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        positions,
    )
    conn.commit()


def _seed_rotation_events(conn: sqlite3.Connection) -> None:
    now_s = _now_s()
    conn.executemany(
        "INSERT INTO loop_rotation_events (ts, venue, victim_symbol, "
        "victim_strategy, winner_symbol, e_new, e_held, margin, cost) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (now_s - 50, "okx", "DOGE-USDT", "tsmom", "BTC-USDT",
             12.5, 4.0, 5.0, 1.2),
            # the most recent fire (largest ts) — this is the "last rotation".
            (now_s - 10, "capital", "GBPUSD", "fx_breakout_basket", "XAUUSD",
             30.0, 8.0, 5.0, 2.5),
        ],
    )
    conn.commit()


def _seed_session_exit_events(conn: sqlite3.Connection) -> None:
    now_s = _now_s()
    conn.executemany(
        "INSERT INTO loop_session_exit_events (ts, venue, symbol) "
        "VALUES (?,?,?)",
        [
            (now_s - 40, "capital", "EURUSD"),
            (now_s - 20, "alpaca", "AAPL"),
            (now_s - 5, "alpaca", "MSFT"),
        ],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Per-stream OPEN vs CLOSED split
# ---------------------------------------------------------------------------


def test_per_stream_closed_n_counts_closed_fills(tmp_path: Path) -> None:
    """closed_n per stream == that venue's closed-fill count (open_n unchanged)."""
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    _seed_fills_positions(conn)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    by_id = {s.stream_id: s for s in streams}
    # okx: 2 closed trades, 1 open ; capital: 1 closed, 1 open ; alpaca: 0/0.
    assert by_id["A_okx_crypto"].closed_n == 2
    assert by_id["A_okx_crypto"].open_positions_n == 1
    assert by_id["B_capital_cfd"].closed_n == 1
    assert by_id["B_capital_cfd"].open_positions_n == 1
    assert by_id["C_alpaca_equity"].closed_n == 0
    assert by_id["C_alpaca_equity"].open_positions_n == 0


def test_per_stream_closed_n_reconciles_to_daily_trades(tmp_path: Path) -> None:
    """closed_n is exactly the existing daily_trades count per lane (no new
    source of truth — same is_close sum, just surfaced under a clearer name)."""
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    _seed_fills_positions(conn)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    for s in streams:
        assert s.closed_n == s.daily_trades


def test_per_stream_recent_closed_list(tmp_path: Path) -> None:
    """Each lane carries a recent-closed list of its own venue's closed trades,
    newest first; empty venues get an empty list (graceful zero)."""
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    _seed_fills_positions(conn)
    try:
        streams = _per_stream_summary(conn, now_s=_now_s())
    finally:
        conn.close()
    by_id = {s.stream_id: s for s in streams}
    okx = by_id["A_okx_crypto"]
    assert len(okx.recent_closed) == 2
    # all rows belong to the okx venue.
    assert all(r.venue == "okx" for r in okx.recent_closed)
    # newest-first ordering (rf3 closed after rf2).
    ts = [r.ts_close for r in okx.recent_closed]
    assert ts == sorted(ts, reverse=True)
    # alpaca empty lane → empty list, not None.
    assert by_id["C_alpaca_equity"].recent_closed == []


# ---------------------------------------------------------------------------
# Rotation telemetry
# ---------------------------------------------------------------------------


def test_rotation_telemetry_count_and_last(tmp_path: Path) -> None:
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    _seed_rotation_events(conn)
    try:
        rot = _rotation_telemetry(conn, now_s=_now_s())
    finally:
        conn.close()
    assert rot.rotation_count == 2
    # last == most-recent ts row (capital / GBPUSD).
    assert rot.last_rotation is not None
    last = rot.last_rotation
    assert last.venue == "capital"
    assert last.victim_symbol == "GBPUSD"
    assert last.winner_symbol == "XAUUSD"
    assert round(last.e_new, 6) == 30.0
    assert round(last.e_held, 6) == 8.0
    assert round(last.margin, 6) == 5.0
    assert round(last.cost, 6) == 2.5


def test_session_forced_exit_count(tmp_path: Path) -> None:
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    _seed_session_exit_events(conn)
    try:
        rot = _rotation_telemetry(conn, now_s=_now_s())
    finally:
        conn.close()
    assert rot.session_forced_exit_count == 3


def test_rotation_telemetry_graceful_zero(tmp_path: Path) -> None:
    """No rotation / forced-exit rows → zeroed telemetry, last_rotation None."""
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    _seed_fills_positions(conn)  # fills/positions but no telemetry rows
    try:
        rot = _rotation_telemetry(conn, now_s=_now_s())
    finally:
        conn.close()
    assert rot.rotation_count == 0
    assert rot.session_forced_exit_count == 0
    assert rot.last_rotation is None


def test_rotation_telemetry_missing_table_graceful(tmp_path: Path) -> None:
    """A DB without the telemetry tables (older schema) must not crash — the
    best-effort reader returns a zeroed telemetry object."""
    db = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(db)
    # deliberately NOT init_db → telemetry tables absent.
    try:
        rot = _rotation_telemetry(conn, now_s=_now_s())
    finally:
        conn.close()
    assert rot.rotation_count == 0
    assert rot.session_forced_exit_count == 0
    assert rot.last_rotation is None


# ---------------------------------------------------------------------------
# End-to-end: collect_snapshot exposes the new fields
# ---------------------------------------------------------------------------


def test_collect_snapshot_exposes_rotation_telemetry(tmp_path: Path) -> None:
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    _seed_fills_positions(conn)
    _seed_rotation_events(conn)
    _seed_session_exit_events(conn)
    conn.close()

    snap = collect_snapshot(db)
    assert snap.rotation_count == 2
    assert snap.session_forced_exit_count == 3
    assert snap.last_rotation is not None
    assert snap.last_rotation.venue == "capital"
    # per-stream open/closed split present + reconciles.
    by_id = {s.stream_id: s for s in snap.streams}
    assert by_id["A_okx_crypto"].closed_n == 2
    assert sum(s.closed_n for s in snap.streams) == snap.daily_trades


def test_collect_snapshot_graceful_zero_telemetry(tmp_path: Path) -> None:
    """Clean DB with no rotation/forced-exit activity → zeroed top-level
    telemetry, all 3 lanes still present with closed_n == 0 each."""
    db = tmp_path / "polaris.sqlite"
    conn = init_db(db)
    conn.close()

    snap = collect_snapshot(db)
    assert snap.rotation_count == 0
    assert snap.session_forced_exit_count == 0
    assert snap.last_rotation is None
    assert len(snap.streams) == len(STREAMS)
    assert all(s.closed_n == 0 for s in snap.streams)
    assert all(s.recent_closed == [] for s in snap.streams)
