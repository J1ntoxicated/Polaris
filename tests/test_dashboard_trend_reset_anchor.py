"""TREND (dual-equity) sparkline anchors at reset_ts, not just session_start
(P0-2, Jin 2026-07-02).

Before this fix ``_build_dual_equity_curve`` always anchored at
``_session_start_ms`` (== earliest fill / DB-restart), so a stamped
measurement-reset (a main-logic batch that should restart the FORWARD
measurement window) did not move the TREND curve — a fill from BEFORE the
reset stayed baked into the curve. This floors the curve's lookback at
``max(session_start, reset_ts)`` when a reset is stamped, gracefully falling
back to the pre-fix session-only anchor when none is (the common case).
DEMO/PAPER; display-only — never a trading path.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.scripts.dashboard.snapshot_q_equity import _build_dual_equity_curve
from polaris.storage.measurement_reset import stamp_measurement_reset
from polaris.storage.schema import ALL_DDL


def _mkdb(tmp_path: Path) -> Path:
    db_path = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()
    return db_path


def _insert_fill(
    db_path: Path, *, fill_id: str, side: str, size_usd: float, pnl_usd: float,
    fee_usd: float, is_close: int, ts_ms: int,
) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, 'okx', 'okx:SOL-USDT', 'tsmom', ?, ?, 150.0, ?, 1.0, ?, ?, "
        "        NULL, ?, ?, 6.6, 1000.0, 'filled')",
        (fill_id, side, size_usd, fee_usd, ts_ms, f"o_{fill_id}", pnl_usd, is_close),
    )
    conn.close()


def test_no_reset_falls_back_to_session_start(tmp_path: Path) -> None:
    """No stamped reset → identical to the pre-fix session-only anchor."""
    db_path = _mkdb(tmp_path)
    now_s = int(time.time())
    now_ms = now_s * 1000
    session_start_ms = now_ms - 3600 * 1000
    _insert_fill(db_path, fill_id="o", side="buy", size_usd=1000.0, pnl_usd=0.0,
                 fee_usd=7.0, is_close=0, ts_ms=session_start_ms)
    _insert_fill(db_path, fill_id="c", side="sell", size_usd=1000.0, pnl_usd=20.0,
                 fee_usd=7.0, is_close=1, ts_ms=now_ms - 60_000)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result = _build_dual_equity_curve(
            conn, now_s=now_s, starting_capital=100_000.0,
        )
    finally:
        conn.close()

    assert result.bucket_ts[0] - session_start_ms // 1000 <= 61
    # Both fills counted (no reset filter applied).
    assert abs(result.total_realised_demo - (20.0 - 2 * 7.0)) < 1e-6


def test_reset_after_session_start_excludes_pre_reset_fill(tmp_path: Path) -> None:
    """A stamped reset AFTER the session start floors the curve at reset_ts —
    a round-trip fully BEFORE the reset must not count."""
    db_path = _mkdb(tmp_path)
    now_s = int(time.time())
    now_ms = now_s * 1000
    session_start_ms = now_ms - 3 * 3600 * 1000   # session began 3h ago
    reset_ts = now_s - 3600                        # reset stamped 1h ago

    # Pre-reset round-trip (2h ago, before the reset) — must be EXCLUDED.
    _insert_fill(db_path, fill_id="o_pre", side="buy", size_usd=1000.0, pnl_usd=0.0,
                 fee_usd=7.0, is_close=0, ts_ms=session_start_ms)
    _insert_fill(db_path, fill_id="c_pre", side="sell", size_usd=1000.0, pnl_usd=999.0,
                 fee_usd=7.0, is_close=1, ts_ms=now_ms - 2 * 3600 * 1000 + 1_000)
    # Post-reset round-trip (30 min ago) — must be INCLUDED.
    _insert_fill(db_path, fill_id="o_post", side="buy", size_usd=1000.0, pnl_usd=0.0,
                 fee_usd=7.0, is_close=0, ts_ms=now_ms - 40 * 60 * 1000)
    _insert_fill(db_path, fill_id="c_post", side="sell", size_usd=1000.0, pnl_usd=20.0,
                 fee_usd=7.0, is_close=1, ts_ms=now_ms - 30 * 60 * 1000)

    conn = sqlite3.connect(db_path, isolation_level=None)
    stamp_measurement_reset(
        conn, label="test reset", git_sha="abc123",
        reset_ts=reset_ts, equity_baseline_usd=100_000.0,
    )
    conn.close()

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result = _build_dual_equity_curve(
            conn, now_s=now_s, starting_capital=100_000.0,
        )
    finally:
        conn.close()

    # Only the post-reset round-trip counts: gross +20, fee 14 → net +6.
    assert abs(result.total_realised_demo - 6.0) < 1e-6
    # Curve floor is at/after the reset instant, not the 3h-old session start.
    assert result.bucket_ts[0] >= reset_ts - 61


def test_reset_before_session_start_is_a_noop(tmp_path: Path) -> None:
    """A reset stamped BEFORE the current session start changes nothing — the
    later anchor (session start) already dominates max()."""
    db_path = _mkdb(tmp_path)
    now_s = int(time.time())
    now_ms = now_s * 1000
    session_start_ms = now_ms - 3600 * 1000   # session began 1h ago
    reset_ts = now_s - 3 * 3600                # reset stamped 3h ago (older)

    _insert_fill(db_path, fill_id="o", side="buy", size_usd=1000.0, pnl_usd=0.0,
                 fee_usd=7.0, is_close=0, ts_ms=session_start_ms)
    _insert_fill(db_path, fill_id="c", side="sell", size_usd=1000.0, pnl_usd=20.0,
                 fee_usd=7.0, is_close=1, ts_ms=now_ms - 60_000)

    conn = sqlite3.connect(db_path, isolation_level=None)
    stamp_measurement_reset(
        conn, label="old reset", git_sha="abc123",
        reset_ts=reset_ts, equity_baseline_usd=100_000.0,
    )
    conn.close()

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result = _build_dual_equity_curve(
            conn, now_s=now_s, starting_capital=100_000.0,
        )
    finally:
        conn.close()

    assert abs(result.total_realised_demo - (20.0 - 14.0)) < 1e-6
    assert result.bucket_ts[0] - session_start_ms // 1000 <= 61
