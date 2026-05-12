"""Day 6 — fills table DDL + persist + recent-fills query tests."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.core.data.fill_normalizer import Fill
from polaris.core.data.fills_persist import (
    make_fill_id,
    persist_fill,
    read_recent_fills,
)
from polaris.storage.schema import init_db


def _fill(*, venue: str = "okx", order_id: str = "ord1", ts_ms: int = 1, **kw) -> Fill:
    base = dict(
        venue=venue,
        instrument_id=f"{venue}:BTC-USDT",
        strategy_id="volume_burst",
        side="buy",
        size_usd=50.0,
        fill_price=80_000.0,
        fee_usd=0.05,
        slippage_bps=2.0,
        ts_ms=ts_ms,
        order_id=order_id,
        client_order_id=None,
        base_qty=0.000625,
        quote_qty=50.0,
        state="filled",
    )
    base.update(kw)
    return Fill(**base)  # type: ignore[arg-type]


def test_fills_schema_create_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        # Re-init must not error.
        conn2 = init_db(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fills)").fetchall()}
        assert {"fill_id", "venue", "instrument_id", "strategy_id", "side",
                "size_usd", "fill_price", "fee_usd", "slippage_bps", "ts_ms",
                "order_id", "contribution_id", "pnl_usd", "is_close"} <= cols
        conn2.close()
    finally:
        conn.close()


def test_persist_fill_writes_one_row(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        f = _fill(ts_ms=int(time.time() * 1000))
        fid = persist_fill(conn, f, is_close=False)
        rows = conn.execute("SELECT COUNT(*) FROM fills").fetchone()
        assert rows[0] == 1
        # fill_id format
        assert fid.startswith("okx:")
        assert ":open" in fid
    finally:
        conn.close()


def test_persist_fill_idempotent_on_same_id(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        f = _fill(ts_ms=1234)
        persist_fill(conn, f, is_close=False)
        persist_fill(conn, f, is_close=False)  # same fill_id -> REPLACE
        n = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_persist_fill_open_and_close_distinct(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        f = _fill(ts_ms=1234)
        persist_fill(conn, f, is_close=False)
        persist_fill(conn, f, is_close=True, pnl_usd=5.0)
        n = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        assert n == 2
        rows = conn.execute(
            "SELECT pnl_usd, is_close FROM fills ORDER BY is_close"
        ).fetchall()
        # First row open (is_close=0), second close (is_close=1, pnl=5)
        assert rows[0][1] == 0
        assert rows[1][1] == 1
        assert rows[1][0] == 5.0
    finally:
        conn.close()


def test_read_recent_fills_orders_desc(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        for i in range(3):
            persist_fill(
                conn, _fill(ts_ms=1000 + i, order_id=f"o{i}"), is_close=False
            )
        rows = read_recent_fills(conn, limit=5)
        assert len(rows) == 3
        assert rows[0]["ts_ms"] == 1002
        assert rows[2]["ts_ms"] == 1000
    finally:
        conn.close()


def test_read_recent_fills_returns_empty_on_missing_table() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        rows = read_recent_fills(conn)
        assert rows == []
    finally:
        conn.close()


def test_make_fill_id_distinguishes_open_close() -> None:
    f = _fill()
    open_id = make_fill_id(f, is_close=False)
    close_id = make_fill_id(f, is_close=True)
    assert open_id != close_id
    assert open_id.endswith(":open")
    assert close_id.endswith(":close")


def test_persist_fill_indexes_exist(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    try:
        idx_names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='fills'"
            ).fetchall()
        }
        assert "idx_fills_ts" in idx_names
        assert "idx_fills_venue_instrument" in idx_names
    finally:
        conn.close()
