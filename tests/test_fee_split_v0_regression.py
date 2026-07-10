"""Behavior-invariant regression — fee-split v0 additive schema must NOT
change the live survival FSM (vault/50_research/debates/
fee_split_judgment_2026-07-10.md R2 item 5: "v0 배포 즉시 거동 변화 0").

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). The
close-hook (``_production_close_classes.update_strategy_class_on_close``)
keeps consuming ONLY ``score_f_events.score_contrib`` (the OLD fee-
normalized axis) — the new ``gross_usd``/``notional_usd``/``fee_raw_usd``
columns are written by ``rollup_score_f`` for measurement/shadow purposes
only and must have ZERO influence on the actual transition decision.
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


def test_intent_scores_sql_reads_only_score_contrib_never_gross_axis():
    """Drift-lock: the close-hook's intent-score query must select ONLY
    score_contrib — a future accidental wire-up of gross_usd/notional_usd
    into the LIVE survival FSM query would trip this test, forcing a
    deliberate decision (not a silent regression)."""
    source = inspect.getsource(_production_close_classes._intent_scores)
    assert "score_contrib" in source
    assert "gross_usd" not in source
    assert "notional_usd" not in source
    assert "fee_raw_usd" not in source


def test_mutating_gross_columns_after_rollup_does_not_change_transition(conn):
    """A closed lifecycle rolls up normally and drives the close-hook's
    transition decision (stays EARN — well above the Schmitt demotion
    threshold). Then the NEW gross_usd/notional_usd/fee_raw_usd columns are
    overwritten with wildly different (adversarial) values via raw SQL,
    WITHOUT touching score_contrib — re-reading the intent-score series the
    live FSM consumes must be BYTE-IDENTICAL before and after, proving those
    columns have zero read-path influence."""
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

    # Adversarially corrupt the NEW columns only (score_contrib untouched).
    conn.execute(
        "UPDATE score_f_events SET gross_usd = -999999.0, "
        "notional_usd = 0.0000001, fee_raw_usd = 999999.0 "
        "WHERE position_id = 'p1'"
    )
    conn.commit()

    scores_after_corruption = _intent_scores(
        conn, venue=VENUE, strategy_id=STRATEGY_ID, window_w=3,
    )
    assert scores_after_corruption == pytest.approx(scores_before_corruption)
