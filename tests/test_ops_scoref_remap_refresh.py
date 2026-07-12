"""Tests for tools/ops/scoref_remap_refresh.py (fee-split v1 FLIP item 4 —
re-derivation cadence sweeper). DEMO/PAPER only."""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.storage.schema import init_db
from tools.ops.scoref_remap_refresh import refresh_all, run_refresh

NOW = 1_800_000_000


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _mk_closed(conn, *, position_id, venue, strategy_id, closed_ts, pnl_usd, size_usd=1000.0):
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts) VALUES (?, ?, 'BTC-USDT', ?, ?, ?, 'long', 1.0, 'closed', ?, ?)",
        (position_id, venue, strategy_id, strategy_id, strategy_id, closed_ts - 3600, closed_ts),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'buy', ?, 100.0, 1.0, ?, ?, ?, ?, 1)",
        (uuid.uuid4().hex, venue, f"{venue}:BTC-USDT", strategy_id, size_usd,
         closed_ts * 1000, uuid.uuid4().hex, position_id, pnl_usd),
    )


def test_refresh_all_writes_track_and_pool_entries(conn):
    for i in range(35):
        pnl = 10.0 if i % 2 == 0 else -5.0
        _mk_closed(conn, position_id=f"p{i}", venue="okx", strategy_id="s1",
                   closed_ts=NOW - i * 100, pnl_usd=pnl)
    conn.commit()

    written = refresh_all(conn, now_ts=NOW)
    assert written >= 2  # at least the track row + the okx pool row

    rows = {
        r[0] for r in conn.execute("SELECT scope_key FROM score_f_remap_table")
    }
    assert "okx:s1" in rows
    assert "okx:__pool__" in rows


def test_refresh_all_idempotent_rerun(conn):
    for i in range(35):
        _mk_closed(conn, position_id=f"p{i}", venue="okx", strategy_id="s1",
                   closed_ts=NOW - i * 100, pnl_usd=10.0)
    conn.commit()
    refresh_all(conn, now_ts=NOW)
    second_written = refresh_all(conn, now_ts=NOW + 100)
    # UPSERT — a re-run still reports a write per eligible scope, no crash,
    # no duplicate rows (scope_key is PRIMARY KEY).
    assert second_written >= 2
    n_rows = conn.execute("SELECT COUNT(*) FROM score_f_remap_table").fetchone()[0]
    assert n_rows == 2  # track + pool, not doubled


def test_run_refresh_returns_tripwire_report(conn):
    written, report = run_refresh(conn, now_ts=NOW)
    assert written == 0  # empty DB — nothing eligible yet
    assert report.shadow_divergence_flag is False
