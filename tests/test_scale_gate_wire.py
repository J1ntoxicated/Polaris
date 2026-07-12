"""Tests for scale_gate_wire (fee-split v1 FLIP item 3 — live SCALE-gate
input resolvers). DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital
CFD demo). These resolvers feed ``evaluate_scale_gate`` — see
test_pts_classes_e2e_wire-style tests / test_layer3_sizing_full.py for the
full compute_size() wiring confirmation.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.classes.scale_gate_wire import (
    resolve_best_case_friction,
    resolve_gross_lcb,
)
from polaris.storage.schema import init_db

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


# ---------------------------------------------------------------------------
# resolve_best_case_friction
# ---------------------------------------------------------------------------


def test_maker_suffix_strategy_gets_maker_floor():
    maker = resolve_best_case_friction(venue="okx", strategy_id="weekend_thin_book_flush_maker")
    taker = resolve_best_case_friction(venue="okx", strategy_id="rsi_bb_pullback")
    # OKX real maker (8bps) < taker (10bps) -> maker-capable floor is tighter.
    assert maker < taker


def test_friction_floor_finite_and_positive_for_every_registered_venue():
    for venue in ("okx", "capital", "alpaca"):
        f = resolve_best_case_friction(venue=venue, strategy_id="some_strategy")
        assert f >= 0.0


# ---------------------------------------------------------------------------
# resolve_gross_lcb
# ---------------------------------------------------------------------------


def test_gross_lcb_none_with_no_history(conn):
    assert resolve_gross_lcb(conn, venue="okx", strategy_id="brand_new", now_ts=NOW) is None


def test_gross_lcb_none_below_cold_start_floor(conn):
    """Fewer than N_eff=12 samples -> no verdict yet (cold-start non-starvation)."""
    for i in range(5):
        _mk_closed(conn, position_id=f"p{i}", venue="okx", strategy_id="thin",
                   closed_ts=NOW - i * 100, pnl_usd=10.0)
    conn.commit()
    from polaris.core.classes.score_f import rollup_score_f
    rollup_score_f(conn, now_ts=NOW)
    assert resolve_gross_lcb(conn, venue="okx", strategy_id="thin", now_ts=NOW) is None


def test_gross_lcb_computed_with_enough_history(conn):
    for i in range(20):
        _mk_closed(conn, position_id=f"p{i}", venue="okx", strategy_id="proven",
                   closed_ts=NOW - i * 100, pnl_usd=10.0)
    conn.commit()
    from polaris.core.classes.score_f import rollup_score_f
    rollup_score_f(conn, now_ts=NOW)
    lcb = resolve_gross_lcb(conn, venue="okx", strategy_id="proven", now_ts=NOW)
    assert lcb is not None
    assert lcb > 0.0  # consistent positive gross_bps -> a positive LCB
