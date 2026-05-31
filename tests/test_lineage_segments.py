"""Lineage read-model (P3 self-evolve) — TDD.

Covers ticker ↔ strategy ↔ exit lineage recording into
``position_strategy_segments``:
  - open → exit segment lifecycle (entry_ts set, exit_ts NULL → stamped).
  - strategy swap closes the prior segment + opens a new one (transition).
  - cell_key is exactly ``venue:strategy:ticker:regime``.
  - query helpers ``lineage_for_cell`` / ``recent_lineage``.

Behaviour-0: this table is a read-model — live trading never reads it.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.lineage import (
    build_cell_key,
    lineage_for_cell,
    recent_lineage,
    record_segment_close,
    record_segment_open,
)
from polaris.core.live_recalc import (
    SwapCandidate,
    detect_regime_flip,
    evaluate_strategy_swap,
)
from polaris.storage.schema import init_db


@pytest.fixture
def memdb() -> sqlite3.Connection:
    return init_db(":memory:")


# ---------------------------------------------------------------------------
# cell_key
# ---------------------------------------------------------------------------


def test_cell_key_exact_format() -> None:
    assert (
        build_cell_key(
            venue="okx", strategy_id="volume_burst", ticker="BTC-USDT",
            regime="bull_trend",
        )
        == "okx:volume_burst:BTC-USDT:bull_trend"
    )


# ---------------------------------------------------------------------------
# open → exit lifecycle
# ---------------------------------------------------------------------------


def test_open_then_close_lineage(memdb: sqlite3.Connection) -> None:
    seg_id = record_segment_open(
        memdb, position_id="pos1", trade_id="pos1", venue="okx",
        ticker="BTC-USDT", strategy_id="volume_burst", regime="bull_trend",
        entry_ts=1000,
    )
    # Open row: exit_ts NULL, cell_key derived, pnl zeroed.
    row = memdb.execute(
        "SELECT ended_ts, exit_reason, pnl_r, pnl_usd, cell_key, venue, ticker, "
        "trade_id, regime_at_start, started_ts, entry_reason "
        "FROM position_strategy_segments WHERE segment_id = ?",
        (seg_id,),
    ).fetchone()
    assert row[0] is None  # ended_ts (exit_ts) NULL while open
    assert row[1] is None  # exit_reason
    assert row[2] == pytest.approx(0.0)
    assert row[3] == pytest.approx(0.0)
    assert row[4] == "okx:volume_burst:BTC-USDT:bull_trend"
    assert row[5] == "okx"
    assert row[6] == "BTC-USDT"
    assert row[7] == "pos1"
    assert row[8] == "bull_trend"
    assert row[9] == 1000
    assert row[10] == "entry"

    # Close stamps exit_ts / exit_reason / realised pnl.
    record_segment_close(
        memdb, position_id="pos1", exit_ts=1500, exit_reason="atr_trail_stop",
        pnl_r=1.8, pnl_usd=42.5,
    )
    row2 = memdb.execute(
        "SELECT ended_ts, exit_reason, pnl_r, pnl_usd "
        "FROM position_strategy_segments WHERE segment_id = ?",
        (seg_id,),
    ).fetchone()
    assert row2[0] == 1500
    assert row2[1] == "atr_trail_stop"
    assert row2[2] == pytest.approx(1.8)
    assert row2[3] == pytest.approx(42.5)


def test_close_only_touches_open_segments(memdb: sqlite3.Connection) -> None:
    """A second close call must not overwrite an already-closed segment."""
    seg_id = record_segment_open(
        memdb, position_id="pos1", trade_id="pos1", venue="okx",
        ticker="ETH-USDT", strategy_id="tsmom", regime="chop", entry_ts=10,
    )
    record_segment_close(
        memdb, position_id="pos1", exit_ts=20, exit_reason="loser_timeout",
        pnl_r=-0.5, pnl_usd=-3.0,
    )
    # Second close (e.g. spurious re-call) must be a no-op on the closed row.
    record_segment_close(
        memdb, position_id="pos1", exit_ts=999, exit_reason="OVERWRITE",
        pnl_r=9.9, pnl_usd=99.0,
    )
    row = memdb.execute(
        "SELECT ended_ts, exit_reason, pnl_r FROM position_strategy_segments "
        "WHERE segment_id = ?",
        (seg_id,),
    ).fetchone()
    assert row[0] == 20
    assert row[1] == "loser_timeout"
    assert row[2] == pytest.approx(-0.5)


# ---------------------------------------------------------------------------
# strategy swap transition (prior segment close + new segment open)
# ---------------------------------------------------------------------------


def _seed_position(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO positions "
        "(position_id, venue, symbol, side, qty, status, opened_ts, "
        " strategy_id, entry_strategy_id, active_strategy_id, swap_count, "
        " underlying_group_id) "
        "VALUES ('pos1','okx','BTC-USDT','long',1.0,'open',100,"
        " 'volume_burst','volume_burst','volume_burst',0,'crypto:BTC')"
    )


def test_swap_closes_open_and_opens_new(memdb: sqlite3.Connection) -> None:
    _seed_position(memdb)
    # Seed regime SSOT so the swap inherits regime_at_start (Layer 6 §Q2 key =
    # venue × underlying_group_id) — matches production swap-apply semantics.
    detect_regime_flip(
        memdb, venue="okx", underlying_group_id="crypto:BTC",
        candidate="bull_trend", now_ts=50,
    )
    # Lineage open row from the position-open hook.
    record_segment_open(
        memdb, position_id="pos1", trade_id="pos1", venue="okx",
        ticker="BTC-USDT", strategy_id="volume_burst", regime="bull_trend",
        entry_ts=100,
    )
    cand = SwapCandidate(
        position_id="pos1", from_strategy_id="volume_burst",
        to_strategy_id="spot_donchian", venue="okx", symbol="BTC-USDT",
        side="long", from_correlation_group="spot_breakout",
        to_correlation_group="spot_breakout",
    )
    d = evaluate_strategy_swap(memdb, candidate=cand, now_ts=200, apply=True)
    assert d.decision == "applied"

    rows = memdb.execute(
        "SELECT strategy_id, ended_ts, exit_reason, venue, ticker, cell_key "
        "FROM position_strategy_segments WHERE position_id = 'pos1' "
        "ORDER BY started_ts ASC, ended_ts ASC"
    ).fetchall()
    # Prior (volume_burst) segment is closed by the swap; new (spot_donchian)
    # segment is open.
    by_strat = {r[0]: r for r in rows}
    assert "volume_burst" in by_strat
    assert "spot_donchian" in by_strat
    prior = by_strat["volume_burst"]
    new = by_strat["spot_donchian"]
    assert prior[1] == 200          # prior closed at swap ts
    assert prior[2] == "swap"       # closed with swap reason
    assert new[1] is None           # new segment open
    assert new[3] == "okx"
    assert new[4] == "BTC-USDT"
    assert new[5] == "okx:spot_donchian:BTC-USDT:bull_trend"


# ---------------------------------------------------------------------------
# query helpers
# ---------------------------------------------------------------------------


def test_lineage_for_cell_and_recent(memdb: sqlite3.Connection) -> None:
    record_segment_open(
        memdb, position_id="p1", trade_id="p1", venue="okx", ticker="BTC-USDT",
        strategy_id="volume_burst", regime="bull_trend", entry_ts=10,
    )
    record_segment_open(
        memdb, position_id="p2", trade_id="p2", venue="okx", ticker="BTC-USDT",
        strategy_id="volume_burst", regime="bull_trend", entry_ts=20,
    )
    record_segment_open(
        memdb, position_id="p3", trade_id="p3", venue="capital", ticker="GOLD",
        strategy_id="xau_indices", regime="crisis", entry_ts=30,
    )
    cell = "okx:volume_burst:BTC-USDT:bull_trend"
    segs = lineage_for_cell(memdb, cell)
    assert len(segs) == 2
    assert {s.position_id for s in segs} == {"p1", "p2"}
    assert all(s.cell_key == cell for s in segs)
    # most-recent first
    assert segs[0].entry_ts == 20

    recent = recent_lineage(memdb, n=2)
    assert len(recent) == 2
    assert recent[0].position_id == "p3"  # newest started_ts
    assert recent[1].position_id == "p2"
