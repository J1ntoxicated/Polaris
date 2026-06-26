"""recalc_excursions script — historical anchored re-stamping (dry-run default).

DEMO/PAPER. The correction script re-denominates persisted mfe_r/mae_r from
tracked peak/trough using the ENTRY-time ATR anchor (back-computed from bars
at opened_ts on the entry strategy's timeframe). Dry-run must be provably
read-only; --apply must be idempotent (the stamped anchor column short-
circuits the 2nd run onto path ①). Telemetry-only: positions.mfe_r/mae_r/
entry_atr_pct/entry_atr_timeframe — fills/sizing/gating untouched.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path

import pytest

from polaris.scripts.recalc_excursions import main, run_correction
from polaris.storage.schema import init_db

NOW = 1_780_400_000
OPENED = NOW - 7_200


def _seed_db(db: Path) -> None:
    conn = init_db(db)
    # 1H bars BEFORE opened_ts (the anchor window) — band 2.0 → atr 0.04.
    for i in range(20):
        ts = OPENED - (20 - i) * 3600
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1H', ?, "
            " 100.0, 102.0, 98.0, 100.0, 100.0, 1e4, 1, 100.0, 100.0, 100.0, "
            " 1.0, 'rest')",
            (ts,),
        )
    # Position with DISTORTED excursions (the 1m-contraction inflation):
    # true move peak 103 / trough 99 on anchor 0.04 → mfe 0.375 / mae -0.125.
    conn.execute(
        "INSERT INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " swap_count, peak_price, trough_price, mfe_r, mae_r) "
        "VALUES ('pos-distort', 'okx', 'BTC-USDT', 'crypto:BTC', 'ema_crossover', "
        " 'ema_crossover', 'ema_crossover', 'long', 0.001, 'closed', ?, 0, 103.0, 99.0, "
        " 15.0, -5.0)",
        (OPENED,),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'ema_crossover', 'okx:BTC-USDT', 'okx', 'long', 0.001, 100.0, "
        " 80.0, 0.05, 1.0, 0.0, 0, 'pos-distort', ?, 'filled')",
        (uuid.uuid4().hex, OPENED * 1000, uuid.uuid4().hex),
    )
    # Row without extremes (pre-#26) → skipped_no_extremes.
    conn.execute(
        "INSERT INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " swap_count) "
        "VALUES ('pos-noext', 'okx', 'BTC-USDT', 'crypto:BTC', 'ema_crossover', "
        " 'ema_crossover', 'ema_crossover', 'long', 0.001, 'closed', ?, 0)",
        (OPENED,),
    )
    # Row with extremes but NO entry fill → skipped_no_fill.
    conn.execute(
        "INSERT INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " swap_count, peak_price, trough_price) "
        "VALUES ('pos-nofill', 'okx', 'BTC-USDT', 'crypto:BTC', 'ema_crossover', "
        " 'ema_crossover', 'ema_crossover', 'long', 0.001, 'closed', ?, 0, 101.0, 99.5)",
        (OPENED,),
    )
    # Row on an instrument with NO bars → skipped_no_bars.
    conn.execute(
        "INSERT INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " swap_count, peak_price, trough_price) "
        "VALUES ('pos-nobars', 'okx', 'XYZ-USDT', 'crypto:XYZ', 'ema_crossover', "
        " 'ema_crossover', 'ema_crossover', 'long', 0.001, 'closed', ?, 0, 101.0, 99.5)",
        (OPENED,),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'ema_crossover', 'okx:XYZ-USDT', 'okx', 'long', 0.001, 100.0, "
        " 80.0, 0.05, 1.0, 0.0, 0, 'pos-nobars', ?, 'filled')",
        (uuid.uuid4().hex, OPENED * 1000, uuid.uuid4().hex),
    )
    conn.close()


def _digest(db: Path) -> str:
    return hashlib.sha256(db.read_bytes()).hexdigest()


def test_dry_run_reports_but_never_writes(tmp_path: Path) -> None:
    db = tmp_path / "polaris.sqlite"
    _seed_db(db)
    before = _digest(db)
    rc = main(["--db", str(db)])
    assert rc == 0
    assert _digest(db) == before  # byte-identical DB — provably read-only
    # The same dry-run computation surfaces the corrected values.
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    report = run_correction(conn, apply=False)
    conn.close()
    by_id = {c.position_id: c for c in report.rows}
    c = by_id["pos-distort"]
    assert c.anchor == pytest.approx(0.04)
    assert c.anchor_source == "bars_1H"
    assert c.new_mfe_r == pytest.approx(3.0 / (100.0 * 0.04 * 2.0))
    assert c.new_mae_r == pytest.approx(-1.0 / (100.0 * 0.04 * 2.0))
    assert report.skipped_no_fill == 1
    assert report.skipped_no_bars == 1
    assert report.skipped_no_extremes >= 1
    assert report.applied is False


def test_apply_stamps_and_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "polaris.sqlite"
    _seed_db(db)
    rc = main(["--db", str(db), "--apply"])
    assert rc == 0
    conn = sqlite3.connect(db, isolation_level=None)
    row = conn.execute(
        "SELECT mfe_r, mae_r, entry_atr_pct, entry_atr_timeframe "
        "FROM positions WHERE position_id = 'pos-distort'",
    ).fetchone()
    assert row[0] == pytest.approx(0.375)
    assert row[1] == pytest.approx(-0.125)
    assert row[2] == pytest.approx(0.04)
    assert row[3] == "1H"
    conn.close()
    # Second apply: path ① (anchor column) → zero corrections, same values.
    digest_after_first = _digest(db)
    rc = main(["--db", str(db), "--apply"])
    assert rc == 0
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    report = run_correction(conn, apply=False)
    conn.close()
    by_id = {c.position_id: c for c in report.rows}
    assert by_id["pos-distort"].anchor_source == "column"
    assert report.corrected == 0
    assert _digest(db) == digest_after_first


def test_apply_touches_only_target_columns(tmp_path: Path) -> None:
    db = tmp_path / "polaris.sqlite"
    _seed_db(db)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    fills_before = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(pnl_usd), 0) FROM fills"
    ).fetchone()
    pos_before = conn.execute(
        "SELECT position_id, status, qty, opened_ts, peak_price, trough_price "
        "FROM positions ORDER BY position_id",
    ).fetchall()
    conn.close()
    assert main(["--db", str(db), "--apply"]) == 0
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(pnl_usd), 0) FROM fills"
    ).fetchone() == fills_before
    assert conn.execute(
        "SELECT position_id, status, qty, opened_ts, peak_price, trough_price "
        "FROM positions ORDER BY position_id",
    ).fetchall() == pos_before
    conn.close()


def test_missing_db_refused(tmp_path: Path) -> None:
    rc = main(["--db", str(tmp_path / "typo.sqlite")])
    assert rc == 2
    assert not (tmp_path / "typo.sqlite").exists()  # never auto-created


def test_limit_bounds_row_scan(tmp_path: Path) -> None:
    db = tmp_path / "polaris.sqlite"
    _seed_db(db)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    report = run_correction(conn, apply=False, limit=1)
    conn.close()
    assert len(report.rows) <= 1
