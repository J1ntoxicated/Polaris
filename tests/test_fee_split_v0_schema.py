"""Tests for the fee-split v0 additive schema (score_f_events.gross_usd /
notional_usd / fee_raw_usd) — vault/50_research/debates/
fee_split_judgment_2026-07-10.md item 7.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
ADDITIVE ONLY: legacy net_usd/fee_denom_usd/score_contrib columns and every
value they hold are UNCHANGED — covered by the existing test_score_f.py
suite continuing to pass unmodified. This file covers ONLY the new columns.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.classes.score_f import rollup_score_f
from polaris.storage.schema import ALL_DDL, init_db


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _mk_position(conn, *, position_id, venue="okx", symbol="BTC-USDT",
                  strategy_id="s1", closed_ts=1_700_003_600) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts) VALUES (?, ?, ?, ?, ?, ?, 'long', 1.0, 'closed', ?, ?)",
        (position_id, venue, symbol, strategy_id, strategy_id, strategy_id,
         1_700_000_000, closed_ts),
    )


def _mk_fill(conn, *, position_id, venue, symbol, strategy_id, size_usd,
             fee_usd, pnl_usd, ts_ms) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'buy', ?, 100.0, ?, ?, ?, ?, ?, 1)",
        (uuid.uuid4().hex, venue, f"{venue}:{symbol}", strategy_id, size_usd,
         fee_usd, ts_ms, uuid.uuid4().hex, position_id, pnl_usd),
    )


# ---------------------------------------------------------------------------
# DDL idempotency + column presence
# ---------------------------------------------------------------------------


def test_ddl_alone_has_new_columns(tmp_path):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(score_f_events)")}
    assert {"gross_usd", "notional_usd", "fee_raw_usd"}.issubset(cols)


def test_init_db_twice_idempotent_with_new_columns(tmp_path):
    db = tmp_path / "idem.sqlite"
    init_db(db)
    conn = init_db(db)  # second full init_db pass on the SAME file
    cols = {row[1] for row in conn.execute("PRAGMA table_info(score_f_events)")}
    assert {"gross_usd", "notional_usd", "fee_raw_usd"}.issubset(cols)


def test_legacy_db_migration_backfills_columns_as_null(tmp_path):
    """A DB built BEFORE the fee-split v0 columns existed (simulated by the
    pre-migration DDL string with the columns stripped) gets them added via
    the idempotent ALTER, defaulting existing rows to NULL (never a
    fabricated 0.0 that would be mistaken for a real measured gross)."""
    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db), isolation_level=None)
    legacy_ddl = (
        "CREATE TABLE score_f_events (position_id TEXT PRIMARY KEY, "
        "venue TEXT NOT NULL, strategy_id TEXT NOT NULL, day TEXT NOT NULL, "
        "closed_ts INTEGER NOT NULL, net_usd REAL NOT NULL DEFAULT 0.0, "
        "fee_denom_usd REAL NOT NULL DEFAULT 0.0001, "
        "score_contrib REAL NOT NULL DEFAULT 0.0)"
    )
    conn.execute(legacy_ddl)
    conn.execute(
        "INSERT INTO score_f_events (position_id, venue, strategy_id, day, "
        "closed_ts, net_usd, fee_denom_usd, score_contrib) "
        "VALUES ('p_legacy', 'okx', 's1', '2026-07-01', 1700000000, 50.0, 1.0, 50.0)"
    )
    conn.close()

    reopened = init_db(db)
    row = reopened.execute(
        "SELECT gross_usd, notional_usd, fee_raw_usd FROM score_f_events "
        "WHERE position_id = 'p_legacy'"
    ).fetchone()
    assert row == (None, None, None)
    # legacy columns untouched
    old = reopened.execute(
        "SELECT net_usd, fee_denom_usd, score_contrib FROM score_f_events "
        "WHERE position_id = 'p_legacy'"
    ).fetchone()
    assert old == (50.0, 1.0, 50.0)


# ---------------------------------------------------------------------------
# rollup_score_f populates the new columns for NEW rows
# ---------------------------------------------------------------------------


def test_rollup_populates_gross_notional_fee_raw_for_new_rows(conn):
    _mk_position(conn, position_id="p1")
    _mk_fill(conn, position_id="p1", venue="okx", symbol="BTC-USDT",
              strategy_id="s1", size_usd=1000.0, fee_usd=5.0, pnl_usd=100.0,
              ts_ms=1_700_003_000_000)

    rollup_score_f(conn, now_ts=1_700_010_000)
    row = conn.execute(
        "SELECT gross_usd, notional_usd, fee_raw_usd, net_usd, fee_denom_usd "
        "FROM score_f_events WHERE position_id = 'p1'"
    ).fetchone()
    gross_usd, notional_usd, fee_raw_usd, net_usd, fee_denom_usd = row
    # gross_usd mirrors net_usd (fills.pnl_usd is already fee-exclusive)
    assert gross_usd == pytest.approx(net_usd) == pytest.approx(100.0)
    assert notional_usd == pytest.approx(1000.0)
    # fee_raw_usd is the UNFLOORED real fee (5.0), distinct from the floored
    # fee_denom_usd used by the OLD score axis (max(5.0, 0.0001*1000)=5.0 here
    # too, since the real fee dominates the floor at this notional).
    assert fee_raw_usd == pytest.approx(5.0)
    assert fee_denom_usd == pytest.approx(5.0)


def test_rollup_gross_usd_distinct_from_fee_denom_when_floor_binds(conn):
    """When the notional floor dominates, fee_denom_usd (floored) diverges
    from fee_raw_usd (unfloored real fee) — proving the new column is NOT
    just a duplicate read of the old one."""
    _mk_position(conn, position_id="p2")
    _mk_fill(conn, position_id="p2", venue="okx", symbol="BTC-USDT",
              strategy_id="s1", size_usd=10_000.0, fee_usd=0.0001, pnl_usd=50.0,
              ts_ms=1_700_003_000_000)

    rollup_score_f(conn, now_ts=1_700_010_000)
    row = conn.execute(
        "SELECT fee_raw_usd, fee_denom_usd FROM score_f_events WHERE position_id = 'p2'"
    ).fetchone()
    fee_raw_usd, fee_denom_usd = row
    assert fee_raw_usd == pytest.approx(0.0001)
    assert fee_denom_usd == pytest.approx(1.0)  # 0.0001 * 10_000 floor wins
    assert fee_raw_usd != pytest.approx(fee_denom_usd)
