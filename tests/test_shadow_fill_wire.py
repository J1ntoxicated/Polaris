"""Tests for pessimistic shadow fill recording (pts-classes group WIRE, item 5).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
Aggressive bias preserved — BENCH/shadow-routed intents keep signaling/
learning; this only records the hypothetical outcome for the reentry
ladder (no_block_filter_architecture / flow_not_block).
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from polaris.core.classes.shadow_fill import (
    pessimistic_round_trip_cost_r,
    record_shadow_fill,
)
from polaris.storage.schema import init_db


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _seed(conn: sqlite3.Connection, *, window_w: int = 3, shadow_ring: str = "[]") -> None:
    conn.execute(
        "INSERT INTO strategy_class (venue, strategy_id, window_w, shadow_ring) "
        "VALUES ('okx', 'rsi_bb_pullback', ?, ?)",
        (window_w, shadow_ring),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# pessimistic_round_trip_cost_r — pure fill-price formula
# ---------------------------------------------------------------------------


def test_market_order_costs_more_than_limit():
    market_r = pessimistic_round_trip_cost_r(venue="okx", order_style="market", atr_pct=0.02)
    limit_r = pessimistic_round_trip_cost_r(venue="okx", order_style="limit", atr_pct=0.02)
    assert market_r > limit_r


def test_cost_is_always_positive():
    assert pessimistic_round_trip_cost_r(venue="capital", order_style="limit", atr_pct=0.01) > 0.0


def test_non_positive_atr_floors_instead_of_dividing_by_zero():
    cost = pessimistic_round_trip_cost_r(venue="okx", order_style="market", atr_pct=0.0)
    assert cost > 0.0
    import math
    assert math.isfinite(cost)


# ---------------------------------------------------------------------------
# record_shadow_fill — wiring / persistence
# ---------------------------------------------------------------------------


def test_appends_one_entry_to_empty_ring(conn):
    _seed(conn)
    record_shadow_fill(
        conn, venue="okx", strategy_id="rsi_bb_pullback", ticker="BTC-USDT",
        regime="chop", order_style="market", atr_pct=0.02,
    )
    row = conn.execute(
        "SELECT shadow_ring FROM strategy_class WHERE venue='okx' AND strategy_id='rsi_bb_pullback'"
    ).fetchone()
    ring = json.loads(row[0])
    assert len(ring) == 1


def test_never_fabricates_a_win_with_no_posterior_history(conn):
    """No learner_posterior row -> mu defaults to 0.0 (neutral), so the
    recorded score is exactly -pessimistic_cost (never positive)."""
    _seed(conn)
    record_shadow_fill(
        conn, venue="okx", strategy_id="rsi_bb_pullback", ticker="BTC-USDT",
        regime="chop", order_style="market", atr_pct=0.02,
    )
    ring = json.loads(
        conn.execute(
            "SELECT shadow_ring FROM strategy_class WHERE venue='okx' AND strategy_id='rsi_bb_pullback'"
        ).fetchone()[0]
    )
    assert ring[0] < 0.0


def test_ring_trimmed_to_window_w(conn):
    _seed(conn, window_w=2)
    for _ in range(5):
        record_shadow_fill(
            conn, venue="okx", strategy_id="rsi_bb_pullback", ticker="BTC-USDT",
            regime="chop", order_style="limit", atr_pct=0.02,
        )
    ring = json.loads(
        conn.execute(
            "SELECT shadow_ring FROM strategy_class WHERE venue='okx' AND strategy_id='rsi_bb_pullback'"
        ).fetchone()[0]
    )
    assert len(ring) == 2


def test_reads_real_posterior_mu_when_present(conn):
    _seed(conn)
    conn.execute(
        "INSERT INTO learner_posterior (exchange, strategy, ticker, regime, mu) "
        "VALUES ('okx', 'rsi_bb_pullback', 'BTC-USDT', 'chop', 5.0)"
    )
    conn.commit()
    record_shadow_fill(
        conn, venue="okx", strategy_id="rsi_bb_pullback", ticker="BTC-USDT",
        regime="chop", order_style="limit", atr_pct=0.02,
    )
    ring = json.loads(
        conn.execute(
            "SELECT shadow_ring FROM strategy_class WHERE venue='okx' AND strategy_id='rsi_bb_pullback'"
        ).fetchone()[0]
    )
    cost = pessimistic_round_trip_cost_r(venue="okx", order_style="limit", atr_pct=0.02)
    assert ring[0] == pytest.approx(5.0 - cost)


def test_no_strategy_class_row_is_a_silent_noop(conn):
    record_shadow_fill(
        conn, venue="okx", strategy_id="unknown", ticker="BTC-USDT",
        regime="chop", order_style="market", atr_pct=0.02,
    )
    row = conn.execute(
        "SELECT COUNT(*) FROM strategy_class WHERE strategy_id='unknown'"
    ).fetchone()
    assert row[0] == 0
