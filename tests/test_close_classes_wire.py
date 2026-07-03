"""Tests for the pts-classes close-event hook (group WIRE).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
Aggressive bias preserved — this hook only ROUTES capital (updates
``strategy_class``), never blocks/throttles a strategy
(aggressive_always_profit / no_block_filter_architecture). Verifies the
end-to-end wiring the task requires: a fake close event -> score_F rollup ->
transition evaluation -> ``strategy_class`` UPDATE -> the NEXT
``resolve_strategy_class`` read reflects the new class.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

import pytest

from polaris.core.sizing.probe_notional import resolve_strategy_class
from polaris.scripts._production_close_classes import update_strategy_class_on_close
from polaris.storage.schema import init_db

STRATEGY_ID = "gold_riskoff_trend_amplify"  # registered, venue=capital
VENUE = "capital"


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


@dataclass
class _State:
    """Minimal stand-in for ProdLoopState (the hook only needs duck typing)."""


@dataclass
class _Trade:
    venue: str
    strategy_id: str


def _upsert_class(
    conn: sqlite3.Connection,
    *,
    strategy_class: str = "EARN",
    window_w: int = 3,
    dwell: int = 0,
    ladder_step: int = 0,
    epoch_id: int = 1,
    last_transition_ts: int = 1_700_000_000,
    kill_state: str = "ACTIVE",
    shadow_ring: str = "[]",
) -> None:
    conn.execute(
        """
        INSERT INTO strategy_class
            (venue, strategy_id, strategy_class, window_w, dwell, ladder_step,
             epoch_id, last_transition_ts, kill_state, shadow_ring)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(venue, strategy_id) DO UPDATE SET
            strategy_class=excluded.strategy_class, window_w=excluded.window_w,
            dwell=excluded.dwell, ladder_step=excluded.ladder_step,
            epoch_id=excluded.epoch_id,
            last_transition_ts=excluded.last_transition_ts,
            kill_state=excluded.kill_state, shadow_ring=excluded.shadow_ring
        """,
        (VENUE, STRATEGY_ID, strategy_class, window_w, dwell, ladder_step,
         epoch_id, last_transition_ts, kill_state, shadow_ring),
    )
    conn.commit()


def _mk_closed_position(
    conn: sqlite3.Connection, *, position_id: str, closed_ts: int, pnl_r: float,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts, pnl_r) VALUES (?, ?, 'XAUUSD', ?, ?, ?, 'long', 1.0, "
        "'closed', ?, ?, ?)",
        (position_id, VENUE, STRATEGY_ID, STRATEGY_ID, STRATEGY_ID,
         closed_ts - 3600, closed_ts, pnl_r),
    )
    fee = 1.0
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'buy', 100.0, 100.0, ?, ?, ?, "
        "?, 0.0, 0)",
        (uuid.uuid4().hex, VENUE, f"{VENUE}:XAUUSD", STRATEGY_ID, fee,
         (closed_ts - 3600) * 1000, uuid.uuid4().hex, position_id),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'sell', 100.0, 100.0, ?, ?, ?, "
        "?, ?, 1)",
        (uuid.uuid4().hex, VENUE, f"{VENUE}:XAUUSD", STRATEGY_ID, fee,
         closed_ts * 1000, uuid.uuid4().hex, position_id, pnl_r * 100.0),
    )
    conn.commit()


def test_close_hook_noop_when_no_strategy_class_row(conn):
    """No bootstrapped row yet -> hook is a silent no-op (never crashes the
    close path, nothing to transition)."""
    _mk_closed_position(conn, position_id="p1", closed_ts=1_700_003_600, pnl_r=1.0)
    trade = _Trade(venue=VENUE, strategy_id=STRATEGY_ID)
    update_strategy_class_on_close(conn, trade=trade, now_ts=1_700_003_600, state=_State())
    row = conn.execute(
        "SELECT COUNT(*) FROM strategy_class WHERE venue=? AND strategy_id=?",
        (VENUE, STRATEGY_ID),
    ).fetchone()
    assert row[0] == 0


def test_close_hook_runs_score_f_rollup(conn):
    """The hook rolls up score_F for the closed lifecycle (batch-commit,
    never inline in the close's own transaction)."""
    _upsert_class(conn, strategy_class="EARN", window_w=3)
    _mk_closed_position(conn, position_id="p1", closed_ts=1_700_003_600, pnl_r=1.0)
    trade = _Trade(venue=VENUE, strategy_id=STRATEGY_ID)
    update_strategy_class_on_close(conn, trade=trade, now_ts=1_700_003_600, state=_State())
    events = conn.execute(
        "SELECT COUNT(*) FROM score_f_events WHERE venue=? AND strategy_id=?",
        (VENUE, STRATEGY_ID),
    ).fetchone()
    assert events[0] == 1


def test_close_hook_demotes_earn_to_bench_on_harsh_losses(conn):
    """A losing streak severe enough to clear the Schmitt EARN->BENCH
    threshold flips ``strategy_class`` to BENCH — and the NEXT
    ``resolve_strategy_class`` read (the G5 branch's own read) reflects it."""
    _upsert_class(conn, strategy_class="EARN", window_w=3)
    # 3 harsh losing lifecycles -> full-window mean score_F well below -3.
    for i in range(3):
        ts = 1_700_000_000 + (i + 1) * 3600
        _mk_closed_position(conn, position_id=f"p{i}", closed_ts=ts, pnl_r=-5.0)

    assert resolve_strategy_class(conn, venue=VENUE, strategy_id=STRATEGY_ID) == "EARN"

    trade = _Trade(venue=VENUE, strategy_id=STRATEGY_ID)
    update_strategy_class_on_close(
        conn, trade=trade, now_ts=1_700_000_000 + 4 * 3600, state=_State()
    )

    assert resolve_strategy_class(conn, venue=VENUE, strategy_id=STRATEGY_ID) == "BENCH"


def test_close_hook_increments_dwell_on_no_transition(conn):
    """A single mild close with no transition trigger still advances dwell by
    1 (one more close observed in the current class)."""
    _upsert_class(conn, strategy_class="PROVE", window_w=20, dwell=0)
    _mk_closed_position(conn, position_id="p1", closed_ts=1_700_003_600, pnl_r=0.1)
    trade = _Trade(venue=VENUE, strategy_id=STRATEGY_ID)
    update_strategy_class_on_close(conn, trade=trade, now_ts=1_700_003_600, state=_State())
    row = conn.execute(
        "SELECT dwell, strategy_class FROM strategy_class WHERE venue=? AND strategy_id=?",
        (VENUE, STRATEGY_ID),
    ).fetchone()
    assert row[1] == "PROVE"
    assert row[0] == 1


def test_close_hook_logs_exec_starved_unconditionally(conn, caplog):
    """Regression (pts-classes group G blocker): the close hook's log line
    was gated on ``if changed:``, but EXEC_STARVED is BY CONSTRUCTION an
    unchanged-class result (``_unchanged(inp, reason="EXEC_STARVED")`` in
    transition.py) — so the line could never fire in production, leaving
    log_scan.py's ``EXEC_STARVED`` regex + watchdog.py's ``exec_starved``
    alert permanently dead (0 forever) regardless of real starvation.

    Drives ``n_fills_total`` >= 50 with ``last_50_fill_rate`` == 0.0 (50
    recent signals, zero fills within that signal span) to force the real
    ``_fill_gate_ok`` gate to fail via the PRODUCTION emission path — not a
    hand-written synthetic log line."""
    _upsert_class(conn, strategy_class="EARN", window_w=3)

    # 55 far-past closes -> 110 fills total (well over the n>=50 floor),
    # all timestamped before the 50 recent signals below.
    base_ts = 1_700_000_000
    for i in range(55):
        _mk_closed_position(conn, position_id=f"old{i}", closed_ts=base_ts + i * 60, pnl_r=0.1)

    # 50 recent signals with no fills after them -> last_50_fill_rate == 0.0.
    now_ts = base_ts + 55 * 60 + 3600
    for i in range(50):
        conn.execute(
            "INSERT INTO signals (strategy_id, signal_id, instrument_id, "
            "direction, score, thesis, ts) VALUES (?, ?, ?, 'long', 0.5, 't', ?)",
            (STRATEGY_ID, f"sig-{i}", f"{VENUE}:XAUUSD", now_ts - (50 - i)),
        )
    conn.commit()

    trade = _Trade(venue=VENUE, strategy_id=STRATEGY_ID)
    with caplog.at_level("INFO", logger="polaris.scripts._production_close_classes"):
        update_strategy_class_on_close(conn, trade=trade, now_ts=now_ts, state=_State())

    row = conn.execute(
        "SELECT strategy_class FROM strategy_class WHERE venue=? AND strategy_id=?",
        (VENUE, STRATEGY_ID),
    ).fetchone()
    assert row[0] == "EARN"  # unchanged, as EXEC_STARVED implies

    lines = [
        r.getMessage() for r in caplog.records if "EXEC_STARVED" in r.getMessage()
    ]
    assert lines, "expected an unconditional EXEC_STARVED log line from the close hook"
    assert lines[0] == (
        f"[pts-classes] {VENUE}/{STRATEGY_ID} transition unchanged (EXEC_STARVED)"
    )
    # This is the exact literal log_scan.py's MARKER_PATTERNS["exec_starved"]
    # regex (r"EXEC_STARVED") keys on — confirms the producer -> consumer
    # string actually matches, not just "some log line fired".
    import re
    assert re.compile(r"EXEC_STARVED").search(lines[0])


def test_close_hook_never_raises_on_db_error(conn, monkeypatch):
    """Fail-open: any internal exception is swallowed (mirrors every other
    ``_safe_*`` post-commit helper) — a classification failure must never
    unwind an already-committed close."""
    _upsert_class(conn, strategy_class="EARN")
    trade = _Trade(venue=VENUE, strategy_id=STRATEGY_ID)

    def _boom(*a, **kw):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(
        "polaris.scripts._production_close_classes.rollup_score_f", _boom
    )
    update_strategy_class_on_close(conn, trade=trade, now_ts=1_700_000_000, state=_State())  # no raise
