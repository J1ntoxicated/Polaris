"""Tests for probe_fee_24h accrual (pts-classes group WIRE, item 4)."""
from __future__ import annotations

import sqlite3

import pytest

from polaris.core.classes.probe_fee import accrue_probe_fee
from polaris.storage.schema import init_db


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _seed(conn: sqlite3.Connection, *, probe_fee_24h: float = 0.0) -> None:
    conn.execute(
        "INSERT INTO strategy_class (venue, strategy_id, probe_fee_24h) "
        "VALUES ('okx', 'rsi_bb_pullback', ?)",
        (probe_fee_24h,),
    )
    conn.commit()


def test_accrues_round_trip_fee_onto_existing_row(conn):
    _seed(conn, probe_fee_24h=1.0)
    accrue_probe_fee(conn, venue="okx", strategy_id="rsi_bb_pullback", notional_usd=1000.0)
    row = conn.execute(
        "SELECT probe_fee_24h FROM strategy_class WHERE venue='okx' AND strategy_id='rsi_bb_pullback'"
    ).fetchone()
    assert row[0] > 1.0  # accrued on top of the pre-existing value


def test_accumulates_across_multiple_calls(conn):
    _seed(conn)
    accrue_probe_fee(conn, venue="okx", strategy_id="rsi_bb_pullback", notional_usd=500.0)
    accrue_probe_fee(conn, venue="okx", strategy_id="rsi_bb_pullback", notional_usd=500.0)
    row = conn.execute(
        "SELECT probe_fee_24h FROM strategy_class WHERE venue='okx' AND strategy_id='rsi_bb_pullback'"
    ).fetchone()
    single = row[0]
    conn2 = init_db(":memory:")
    _seed(conn2)
    accrue_probe_fee(conn2, venue="okx", strategy_id="rsi_bb_pullback", notional_usd=500.0)
    half = conn2.execute(
        "SELECT probe_fee_24h FROM strategy_class WHERE venue='okx' AND strategy_id='rsi_bb_pullback'"
    ).fetchone()[0]
    assert single == pytest.approx(2 * half)


def test_no_row_is_a_silent_noop(conn):
    accrue_probe_fee(conn, venue="okx", strategy_id="unknown_strategy", notional_usd=1000.0)
    row = conn.execute(
        "SELECT COUNT(*) FROM strategy_class WHERE strategy_id='unknown_strategy'"
    ).fetchone()
    assert row[0] == 0  # never creates a row


def test_zero_or_negative_notional_is_a_noop(conn):
    _seed(conn, probe_fee_24h=5.0)
    accrue_probe_fee(conn, venue="okx", strategy_id="rsi_bb_pullback", notional_usd=0.0)
    accrue_probe_fee(conn, venue="okx", strategy_id="rsi_bb_pullback", notional_usd=-100.0)
    row = conn.execute(
        "SELECT probe_fee_24h FROM strategy_class WHERE venue='okx' AND strategy_id='rsi_bb_pullback'"
    ).fetchone()
    assert row[0] == 5.0
