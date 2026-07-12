"""Behavior-invariant regression — SUPERSEDED by fee_split_flip_r2_2026-07-12.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). This
file originally locked fee-split v0's "deploy with zero behavior change"
mandate (vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 item
5): the close-hook read ONLY the persisted ``score_contrib`` column, and the
new v0 ``gross_usd``/``notional_usd``/``fee_raw_usd`` columns were
measurement-only with zero read-path influence.

The v1 flip (fee_split_flip_r2_2026-07-12 item 1) intentionally ends that
invariant — ``score_contrib`` IS now gross_bps by default. The R2 rework
round (mixed-scale-ledger fix) went one step further: ``score_f_events`` is
append-only with no backfill, so a pre-flip row's persisted
``score_contrib`` still holds the LEGACY value forever; the close-hook can
no longer trust that column's scale at all and instead reconstructs the
CURRENT judged axis from ``net_usd``/``fee_denom_usd``/``notional_usd`` at
READ time (``judged_score_from_stored``). This file now locks THAT
invariant instead.
"""
from __future__ import annotations

import inspect
import sqlite3
import uuid
from dataclasses import dataclass

import pytest

from polaris.scripts import _production_close_classes
from polaris.scripts._production_close_classes import update_strategy_class_on_close
from polaris.storage.schema import init_db

STRATEGY_ID = "gold_riskoff_trend_amplify"  # registered, venue=capital
VENUE = "capital"


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


@dataclass
class _State:
    pass


@dataclass
class _Trade:
    venue: str
    strategy_id: str


def _upsert_class(conn: sqlite3.Connection, *, window_w: int = 3) -> None:
    conn.execute(
        "INSERT INTO strategy_class (venue, strategy_id, strategy_class, "
        "window_w, dwell, ladder_step, epoch_id, last_transition_ts, "
        "kill_state, shadow_ring) VALUES (?, ?, 'EARN', ?, 0, 0, 1, "
        "1700000000, 'ACTIVE', '[]')",
        (VENUE, STRATEGY_ID, window_w),
    )
    conn.commit()


def _mk_closed_position(conn: sqlite3.Connection, *, position_id: str,
                         closed_ts: int, pnl_usd: float, fee_usd: float,
                         size_usd: float) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts, closed_ts) VALUES (?, ?, 'XAUUSD', ?, ?, ?, 'long', "
        "1.0, 'closed', ?, ?)",
        (position_id, VENUE, STRATEGY_ID, STRATEGY_ID, STRATEGY_ID,
         closed_ts - 3600, closed_ts),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
        "side, size_usd, fill_price, fee_usd, ts_ms, order_id, "
        "contribution_id, pnl_usd, is_close) VALUES (?, ?, ?, ?, 'buy', ?, "
        "100.0, ?, ?, ?, ?, ?, 1)",
        (uuid.uuid4().hex, VENUE, f"{VENUE}:XAUUSD", STRATEGY_ID, size_usd,
         fee_usd, closed_ts * 1000, uuid.uuid4().hex, position_id, pnl_usd),
    )
    conn.commit()


def _run_close(conn: sqlite3.Connection, *, now_ts: int) -> tuple[str, int]:
    update_strategy_class_on_close(
        conn, trade=_Trade(venue=VENUE, strategy_id=STRATEGY_ID), now_ts=now_ts,
        state=_State(),
    )
    row = conn.execute(
        "SELECT strategy_class, dwell FROM strategy_class "
        "WHERE venue = ? AND strategy_id = ?",
        (VENUE, STRATEGY_ID),
    ).fetchone()
    assert row is not None
    return str(row[0]), int(row[1])


def test_intent_scores_sql_never_reads_persisted_score_contrib():
    """Drift-lock (R2 rework, mixed-scale-ledger fix): the close-hook's
    intent-score query must NOT select the persisted score_contrib column —
    score_f_events is append-only/no-backfill, so that column's scale
    depends on when a row was written. It must instead read the raw
    net_usd/fee_denom_usd/notional_usd columns and reconstruct the CURRENT
    judged axis at read time (judged_score_from_stored) — a future
    accidental revert to trusting the stored column verbatim would trip
    this test."""
    source = inspect.getsource(_production_close_classes._intent_scores)
    assert "SELECT score_contrib" not in source
    assert "SELECT net_usd, fee_denom_usd, notional_usd" in source


def test_corrupting_persisted_score_contrib_does_not_change_transition(conn):
    """A closed lifecycle rolls up normally and drives the close-hook's
    transition decision (stays EARN — well above the Schmitt demotion
    threshold). Then the PERSISTED score_contrib column is overwritten with
    a wildly different (adversarial) value via raw SQL, WITHOUT touching
    net_usd/fee_denom_usd/notional_usd — re-reading the intent-score series
    the live FSM consumes must be BYTE-IDENTICAL before and after, proving
    the close-hook reconstructs from the raw columns and never reads the
    (potentially stale-scale) persisted score_contrib."""
    from polaris.scripts._production_close_classes import _intent_scores

    _upsert_class(conn, window_w=3)
    _mk_closed_position(
        conn, position_id="p1", closed_ts=1_700_003_600, pnl_usd=100.0,
        fee_usd=1.0, size_usd=1000.0,
    )
    baseline_class, baseline_dwell = _run_close(conn, now_ts=1_700_010_000)
    scores_before_corruption = _intent_scores(
        conn, venue=VENUE, strategy_id=STRATEGY_ID, window_w=3,
    )
    assert baseline_class == "EARN"
    assert baseline_dwell == 1
    assert scores_before_corruption  # non-empty — sanity the fixture actually scored

    # Adversarially corrupt ONLY the persisted score_contrib column.
    conn.execute(
        "UPDATE score_f_events SET score_contrib = -999999.0 "
        "WHERE position_id = 'p1'"
    )
    conn.commit()

    scores_after_corruption = _intent_scores(
        conn, venue=VENUE, strategy_id=STRATEGY_ID, window_w=3,
    )
    assert scores_after_corruption == pytest.approx(scores_before_corruption)
