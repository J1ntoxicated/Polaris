"""ADR-012 Slice 1 — tuning-log sidecar writers (data/probes.sqlite).

DEMO/PAPER. The writers are CLONED from the shadow_log pattern: FAIL-OPEN —
``conn=None`` / a missing table / any ``sqlite3.Error`` is a no-op (the live
tick must never crash on a log failure). These tests prove: DDL bootstraps the
tables/view/index, both writers persist rows, the backfill UPDATE fills the
outcome cols, and every writer is fail-open on conn=None / missing table.
"""

from __future__ import annotations

import sqlite3

from polaris.core.probes import EngineDecision, ProbeReading
from polaris.core.probes.tuning_log import (
    PROBE_DDL,
    backfill_probe_outcome,
    log_probe_decisions,
    log_probe_readings,
    open_probe_db,
)


def test_ddl_bootstraps_tables_view_index(tmp_path: object) -> None:
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view','index')"
        )
    }
    assert "probe_readings" in names
    assert "probe_decisions" in names
    assert "v_probe_outcomes" in names
    conn.close()


def test_ddl_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    for _ in range(2):
        for stmt in PROBE_DDL:
            conn.execute(stmt)
    conn.close()


def test_log_readings_persists(tmp_path: object) -> None:
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    readings = [
        ProbeReading("profit_taking", "profit", -0.5, 0.8, {"giveback_r": 0.4}),
        ProbeReading("session_hours", "session", -0.2, 0.2, {"seconds_to_close": 600}),
    ]
    log_probe_readings(
        conn, ts=1000, gate_id=6, position_id="p1", readings=readings
    )
    n = conn.execute("SELECT COUNT(*) FROM probe_readings").fetchone()[0]
    assert n == 2
    row = conn.execute(
        "SELECT probe_id, kind, lean, confidence FROM probe_readings "
        "WHERE probe_id='profit_taking'"
    ).fetchone()
    assert row[0] == "profit_taking"
    assert row[1] == "profit"
    assert abs(row[2] - (-0.5)) < 1e-9
    conn.close()


def test_log_decision_persists_with_truth_cols(tmp_path: object) -> None:
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    dec = EngineDecision(
        action="HARVEST", composite_lean=-0.6, contributing=["profit_taking"],
        applied=False,
    )
    log_probe_decisions(
        conn, ts=1000, run_id="r1", position_id="p1", mode="observe",
        decision=dec, pnl_r_at_decision=0.3, pnl_r_truth=0.3,
        mark_source="ws", mark_age_ms=120, exit_state="open",
    )
    row = conn.execute(
        "SELECT mode, action, applied, composite_lean, pnl_r_truth, "
        "realized_pnl_r FROM probe_decisions WHERE position_id='p1'"
    ).fetchone()
    assert row[0] == "observe"
    assert row[1] == "HARVEST"
    assert row[2] == 0  # applied=False → INT 0
    assert abs(row[3] - (-0.6)) < 1e-9
    assert row[5] is None  # realized_pnl_r NULL until backfill
    conn.close()


def test_backfill_fills_outcome(tmp_path: object) -> None:
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    dec = EngineDecision(action="HOLD", composite_lean=0.0, applied=False)
    log_probe_decisions(
        conn, ts=1000, run_id="r1", position_id="p9", mode="observe",
        decision=dec, pnl_r_at_decision=0.1, pnl_r_truth=0.1,
        mark_source="bar", mark_age_ms=0, exit_state="open",
    )
    backfill_probe_outcome(
        conn, position_id="p9", realized_pnl_r=-0.9, close_reason="atr_trail_stop",
        mfe_r_final=0.6, mae_r_final=-1.0, giveback_r=1.5,
        time_to_exit_sec=420, outcome_ts=1500,
    )
    row = conn.execute(
        "SELECT realized_pnl_r, close_reason, giveback_r FROM probe_decisions "
        "WHERE position_id='p9'"
    ).fetchone()
    assert abs(row[0] - (-0.9)) < 1e-9
    assert row[1] == "atr_trail_stop"
    assert abs(row[2] - 1.5) < 1e-9
    conn.close()


# --- fail-open guarantees (cloned from shadow_log) --------------------------


def test_writers_fail_open_on_none_conn() -> None:
    # No exception when conn is None.
    log_probe_readings(None, ts=1, gate_id=6, position_id="p", readings=[])
    log_probe_decisions(
        None, ts=1, run_id="r", position_id="p", mode="observe",
        decision=EngineDecision(action="HOLD", composite_lean=0.0),
        pnl_r_at_decision=0.0, pnl_r_truth=0.0, mark_source="bar",
        mark_age_ms=0, exit_state="open",
    )
    backfill_probe_outcome(
        None, position_id="p", realized_pnl_r=0.0, close_reason="x",
        mfe_r_final=0.0, mae_r_final=0.0, giveback_r=0.0,
        time_to_exit_sec=0, outcome_ts=0,
    )


def test_writers_fail_open_on_missing_table() -> None:
    conn = sqlite3.connect(":memory:")  # no DDL → tables absent
    log_probe_readings(
        conn, ts=1, gate_id=6, position_id="p",
        readings=[ProbeReading("x", "technical", 0.0, 0.0, {})],
    )
    log_probe_decisions(
        conn, ts=1, run_id="r", position_id="p", mode="observe",
        decision=EngineDecision(action="HOLD", composite_lean=0.0),
        pnl_r_at_decision=0.0, pnl_r_truth=0.0, mark_source="bar",
        mark_age_ms=0, exit_state="open",
    )
    backfill_probe_outcome(
        conn, position_id="p", realized_pnl_r=0.0, close_reason="x",
        mfe_r_final=0.0, mae_r_final=0.0, giveback_r=0.0,
        time_to_exit_sec=0, outcome_ts=0,
    )
    conn.close()
