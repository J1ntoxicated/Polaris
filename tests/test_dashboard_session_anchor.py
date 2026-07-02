"""Dashboard PnL/equity/DD/Sharpe are SESSION-anchored, not rolling 24h.

Jin 2026-05-29: the session anchor = ``MIN(fills.ts_ms)`` (clean-restart DB =
current session). Equity curve, daily(=session) realised PnL, drawdown and
Sharpe all measure from that anchor — a fill older than 24h still counts as
long as it belongs to this session, and the curve buckets span only the
session, not a fixed 288×5min/24h grid.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_queries import (
    EQUITY_MIN_BUCKET_SEC,
    _build_equity_curve,
    _session_start_ms,
)
from polaris.storage.schema import ALL_DDL


def _mkdb(tmp_path: Path) -> Path:
    db_path = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()
    return db_path


def _insert_fill(
    db_path: Path,
    *,
    fill_id: str,
    side: str,
    pnl_usd: float,
    fee_usd: float,
    is_close: int,
    ts_ms: int,
) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, 'okx', 'okx:SOL-USDT', 'tsmom', ?, "
        "        1000.0, 150.0, ?, 1.0, ?, ?, NULL, ?, ?, 6.6, 1000.0, 'filled')",
        (fill_id, side, fee_usd, ts_ms, f"o_{fill_id}", pnl_usd, is_close),
    )
    conn.close()


def test_session_start_is_earliest_fill(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    now_ms = int(time.time() * 1000)
    earliest = now_ms - 30 * 3600 * 1000  # 30h ago — older than rolling 24h
    _insert_fill(db_path, fill_id="a", side="buy", pnl_usd=0.0,
                 fee_usd=5.0, is_close=0, ts_ms=earliest)
    _insert_fill(db_path, fill_id="b", side="sell", pnl_usd=50.0,
                 fee_usd=5.0, is_close=1, ts_ms=now_ms - 60_000)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        assert _session_start_ms(conn, now_s=now_ms // 1000) == earliest
    finally:
        conn.close()


def test_session_pnl_counts_fills_older_than_24h(tmp_path: Path) -> None:
    """A round-trip opened 30h ago (this session) must still be in SESSION PnL.

    Pre-change this would have been excluded by the rolling 24h window.
    P0-2 (Jin 2026-07-02): the whole-session sum now lives on
    ``session_pnl_usd`` ('SESSION' on the board) — ``daily_pnl_usd`` ('Today')
    is separately floored at the latest AEST midnight, so a >24h-old fill may
    or may not count there depending on wall-clock (not asserted here).
    """
    db_path = _mkdb(tmp_path)
    now_ms = int(time.time() * 1000)
    open_ms = now_ms - 30 * 3600 * 1000     # 30h ago
    close_ms = now_ms - 29 * 3600 * 1000    # 29h ago
    # open fee 10 + close (gross +100, fee 10) → net +80
    _insert_fill(db_path, fill_id="o", side="buy", pnl_usd=0.0,
                 fee_usd=10.0, is_close=0, ts_ms=open_ms)
    _insert_fill(db_path, fill_id="c", side="sell", pnl_usd=100.0,
                 fee_usd=10.0, is_close=1, ts_ms=close_ms)

    snap = collect_snapshot(db_path=db_path)
    assert abs(snap.session_pnl_usd - 80.0) < 1e-6, (
        f"session PnL must include >24h-old in-session fills: "
        f"got {snap.session_pnl_usd}, expected 80.0"
    )
    assert snap.session_trades == 1


def test_session_curve_spans_only_session(tmp_path: Path) -> None:
    """Curve buckets start at the session anchor, floor 60s; not a 24h grid."""
    db_path = _mkdb(tmp_path)
    now_s = int(time.time())
    now_ms = now_s * 1000
    session_start_ms = now_ms - 10 * 60 * 1000  # 10-min session
    _insert_fill(db_path, fill_id="o", side="buy", pnl_usd=0.0,
                 fee_usd=2.0, is_close=0, ts_ms=session_start_ms)
    _insert_fill(db_path, fill_id="c", side="sell", pnl_usd=20.0,
                 fee_usd=2.0, is_close=1, ts_ms=now_ms - 30_000)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        bucket_ts, equity, total = _build_equity_curve(
            conn, now_s=now_s, starting_capital=100_000.0
        )
    finally:
        conn.close()

    # First bucket end must be within one bucket of the session start, NOT 24h ago.
    assert bucket_ts[0] - session_start_ms // 1000 <= EQUITY_MIN_BUCKET_SEC + 1
    # Last bucket covers ~now.
    assert bucket_ts[-1] >= now_s - EQUITY_MIN_BUCKET_SEC
    # Net realised = +20 gross − 4 fees = +16.
    assert abs(total - 16.0) < 1e-6
    assert abs(equity[-1] - 100_016.0) < 1e-6


def test_no_fills_falls_back_to_now(tmp_path: Path) -> None:
    """Empty DB → session anchor = now, curve is flat at starting capital."""
    db_path = _mkdb(tmp_path)
    now_s = int(time.time())
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        assert _session_start_ms(conn, now_s=now_s) == now_s * 1000
        bucket_ts, equity, total = _build_equity_curve(
            conn, now_s=now_s, starting_capital=130_000.0
        )
    finally:
        conn.close()
    assert total == 0.0
    assert all(abs(v - 130_000.0) < 1e-9 for v in equity)
    assert len(bucket_ts) == len(equity)
