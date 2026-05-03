import os
import sqlite3
import tempfile

import pytest

from invasion.spot import store_spot


@pytest.fixture
def tmp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    monkeypatch.setattr(store_spot, "_DB_PATH", path)
    yield path
    os.unlink(path)


def test_bootstrap_creates_tables(tmp_db):
    store_spot.bootstrap()
    conn = sqlite3.connect(tmp_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"trades", "cell_matrix_spot", "signals"} <= tables


def test_bootstrap_is_idempotent(tmp_db):
    store_spot.bootstrap()
    store_spot.bootstrap()  # should not raise
    conn = sqlite3.connect(tmp_db)
    n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert n == 0


def test_wal_mode_enabled(tmp_db):
    store_spot.bootstrap()
    conn = sqlite3.connect(tmp_db)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_insert_trade_returns_id(tmp_db):
    store_spot.bootstrap()
    trade_id = store_spot.insert_trade({
        "ticker": "BTC", "inst_id": "BTC-USDT", "side": "buy",
        "entry_ts": 1700000000, "size_usd": 100.0,
        "strategy_id": "bb_extreme", "status": "pending_fill",
        "signal_meta": '{"score": 0.7}', "cell_key": "BTC|asia|neutral|bb_extreme|long",
    })
    assert trade_id > 0


def test_update_trade_fill(tmp_db):
    store_spot.bootstrap()
    tid = store_spot.insert_trade({
        "ticker": "ETH", "inst_id": "ETH-USDT", "side": "buy",
        "entry_ts": 1700000000, "size_usd": 100.0,
        "strategy_id": "bb_extreme", "status": "pending_fill",
        "signal_meta": "{}", "cell_key": "k",
    })
    store_spot.update_trade_fill(tid, fill_px=2500.0, qty=0.04,
                                  fill_type="maker", fee_paid=-0.02)
    with store_spot.get_conn() as c:
        row = c.execute(
            "SELECT entry_px, qty, fill_type, fee_paid, status FROM trades WHERE id=?",
            [tid]).fetchone()
    assert row == (2500.0, 0.04, "maker", -0.02, "open")


def test_update_trade_exit(tmp_db):
    store_spot.bootstrap()
    tid = store_spot.insert_trade({
        "ticker": "SOL", "inst_id": "SOL-USDT", "side": "buy",
        "entry_ts": 1700000000, "size_usd": 100.0,
        "strategy_id": "bb_extreme", "status": "open",
        "signal_meta": "{}", "cell_key": "k",
    })
    store_spot.update_trade_fill(tid, fill_px=100.0, qty=1.0,
                                  fill_type="maker", fee_paid=-0.02)
    store_spot.update_trade_exit(tid, exit_px=100.5, exit_ts=1700000300,
                                  net_pnl_usd=0.50, pnl_pct=0.005,
                                  exit_type="TP", fee_exit=-0.02)
    with store_spot.get_conn() as c:
        row = c.execute(
            "SELECT exit_px, exit_type, status, net_pnl_usd FROM trades WHERE id=?",
            [tid]).fetchone()
    assert row == (100.5, "TP", "closed", 0.50)


def test_query_open_trades(tmp_db):
    store_spot.bootstrap()
    for t in ("BTC", "ETH"):
        store_spot.insert_trade({
            "ticker": t, "inst_id": f"{t}-USDT", "side": "buy",
            "entry_ts": 1700000000, "size_usd": 100.0,
            "strategy_id": "bb_extreme", "status": "open",
            "signal_meta": "{}", "cell_key": "k",
        })
    rows = store_spot.query_open_trades()
    assert len(rows) == 2
    assert {r["ticker"] for r in rows} == {"BTC", "ETH"}
