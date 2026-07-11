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


def test_regime_column_exists_on_fresh_probe_db(tmp_path: object) -> None:
    """probe_decisions.regime (backgate-plan W2-d frontgate-consumption
    SHADOW) present + nullable on a fresh sidecar init."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(probe_decisions)")}
    assert "regime" in cols
    conn.close()


def test_regime_column_legacy_probe_db_gets_alter_idempotent(tmp_path: object) -> None:
    """A legacy probe_decisions table (every pre-existing column, no ``regime``)
    is ALTERed in place by ``open_probe_db``; re-opening is safe (duplicate-
    column guarded). Mirrors the full pre-migration shape — ``v_probe_outcomes``
    is created in the SAME ``PROBE_DDL`` pass and reads every outcome column, so
    a legacy fixture must carry them all (not just the columns this test touches)."""
    db = f"{tmp_path}/legacy_probes.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE probe_decisions ("
        "decision_id TEXT PRIMARY KEY, eval_id TEXT NOT NULL, ts INTEGER NOT NULL, "
        "run_id TEXT NOT NULL, position_id TEXT NOT NULL, mode TEXT NOT NULL, "
        "composite_lean REAL NOT NULL, action TEXT NOT NULL, trail_mult REAL, "
        "mfe_protect_json TEXT, widen_atr_mult REAL, profit_target_r REAL, "
        "applied INTEGER NOT NULL DEFAULT 0, pnl_r_at_decision REAL, "
        "pnl_r_truth REAL, mark_source TEXT, mark_age_ms INTEGER, "
        "exit_state TEXT, unit_tag TEXT NOT NULL DEFAULT 'excursion', "
        "ambiguous INTEGER NOT NULL DEFAULT 0, deadband_margin REAL, "
        "quantizer_version TEXT, realized_pnl_r REAL, close_reason TEXT, "
        "mfe_r_final REAL, mae_r_final REAL, giveback_r REAL, "
        "time_to_exit_sec INTEGER, outcome_ts INTEGER)"
    )
    conn.execute(
        "INSERT INTO probe_decisions (decision_id, eval_id, ts, run_id, "
        "position_id, mode, composite_lean, action, applied) VALUES "
        "('d1', 'e1', 1000, 'r1', 'p1', 'observe', 0.0, 'HOLD', 0)"
    )
    conn.commit()
    conn.close()

    conn = open_probe_db(db)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(probe_decisions)")}
        assert "regime" in cols, "missing regime after migrate"
        row = conn.execute(
            "SELECT regime FROM probe_decisions WHERE decision_id = 'd1'"
        ).fetchone()
        assert row[0] is None, "legacy row must backfill regime to NULL"
        conn.close()
        conn = open_probe_db(db)  # re-opening (re-running the ALTER) must not raise
        assert "regime" in {
            row[1] for row in conn.execute("PRAGMA table_info(probe_decisions)")
        }
    finally:
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


def test_log_decision_persists_caller_supplied_regime(tmp_path: object) -> None:
    """A caller that threads ``regime=`` gets it persisted verbatim (not NULL).

    Guards the W2-d fix: ``log_probe_decisions`` must actually write the
    ``ProbeContext.regime`` value it receives, not just accept-and-drop it.
    """
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    dec = EngineDecision(action="HOLD", composite_lean=0.0, applied=False)
    log_probe_decisions(
        conn, ts=1000, run_id="r1", position_id="p-regime", mode="observe",
        decision=dec, pnl_r_at_decision=0.0, pnl_r_truth=0.0,
        mark_source="bar", mark_age_ms=0, exit_state="open",
        regime="trend_up_expansion",
    )
    row = conn.execute(
        "SELECT regime FROM probe_decisions WHERE position_id='p-regime'"
    ).fetchone()
    assert row[0] == "trend_up_expansion"
    conn.close()


def test_log_decision_regime_defaults_to_null_when_omitted(tmp_path: object) -> None:
    """Callers that do not pass ``regime`` keep the legacy NULL semantics."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    dec = EngineDecision(action="HOLD", composite_lean=0.0, applied=False)
    log_probe_decisions(
        conn, ts=1000, run_id="r1", position_id="p-noregime", mode="observe",
        decision=dec, pnl_r_at_decision=0.0, pnl_r_truth=0.0,
        mark_source="bar", mark_age_ms=0, exit_state="open",
    )
    row = conn.execute(
        "SELECT regime FROM probe_decisions WHERE position_id='p-noregime'"
    ).fetchone()
    assert row[0] is None
    conn.close()


def test_observe_probes_threads_ctx_regime_into_tuning_log(tmp_path: object) -> None:
    """The production attach (``observe_probes``) must thread ``ProbeContext.
    regime`` into the persisted ``probe_decisions.regime`` — not drop it on the
    floor between ctx construction and the tuning-log INSERT (W2-d)."""
    from polaris.core.probes.bus import ProbeBus
    from polaris.core.probes.catalog import ProfitTakingProbe
    from polaris.core.probes.engine import ExitEngine
    from polaris.scripts._production_probe_attach import observe_probes

    class _State:
        pass

    state = _State()
    state.probe_bus = ProbeBus([ProfitTakingProbe()])  # type: ignore[attr-defined]
    state.probe_engine = ExitEngine()  # type: ignore[attr-defined]
    state.probe_conn = open_probe_db(f"{tmp_path}/probes.sqlite")  # type: ignore[attr-defined]

    observe_probes(
        state=state,  # type: ignore[arg-type]
        pos={"position_id": "p-thread", "venue": "okx", "symbol": "BTC-USDT"},  # type: ignore[arg-type]
        side="long", entry_price=100.0, last_price=102.0, atr_pct=0.01,
        entry_atr_pct=0.01, pnl_r=1.2, held_seconds=60,
        regime="trend_up_expansion", now_ts=1_000_000, run_id="run-thread",
        mark_source="bar",
    )
    row = state.probe_conn.execute(  # type: ignore[attr-defined]
        "SELECT regime FROM probe_decisions WHERE position_id='p-thread'"
    ).fetchone()
    assert row is not None, "observe_probes must have written a probe_decisions row"
    assert row[0] == "trend_up_expansion"
    state.probe_conn.close()  # type: ignore[attr-defined]


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
