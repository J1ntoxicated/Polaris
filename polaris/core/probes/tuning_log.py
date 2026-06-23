"""ADR-012 — TUNING-LOG sidecar (``data/probes.sqlite``, NOT the live DB).

DEMO/PAPER. A SEPARATE sqlite file (zero WAL contention with the bot writer —
mirrors the W1 ``data/sentinel.sqlite`` sidecar). It records the probe readings
+ the engine's would-be decision per tick, with the outcome cols backfilled at
close, so an OFFLINE ``/debate`` calibration can MEASURE would-be tighten vs
realized giveback BEFORE any knob moves (never auto-applied).

Writers are CLONED from the ``shadow_log`` pattern: FAIL-OPEN — ``conn=None`` /
a missing table / any ``sqlite3.Error`` is swallowed via ``contextlib.suppress``
so the live tick can never crash on a log failure.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from pathlib import Path

from polaris.core.probes import EngineDecision, ProbeReading

__all__ = [
    "PROBE_DDL",
    "backfill_probe_outcome",
    "log_probe_decisions",
    "log_probe_readings",
    "open_probe_db",
]

PROBE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS probe_readings (
        reading_id   TEXT PRIMARY KEY,
        ts           INTEGER NOT NULL,
        gate_id      INTEGER NOT NULL,
        position_id  TEXT NOT NULL,
        probe_id     TEXT NOT NULL,
        kind         TEXT NOT NULL,
        lean         REAL NOT NULL,
        confidence   REAL NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '{}'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS probe_decisions (
        decision_id      TEXT PRIMARY KEY,
        eval_id          TEXT NOT NULL,
        ts               INTEGER NOT NULL,
        run_id           TEXT NOT NULL,
        position_id      TEXT NOT NULL,
        mode             TEXT NOT NULL,
        composite_lean   REAL NOT NULL,
        action           TEXT NOT NULL,
        trail_mult       REAL,
        mfe_protect_json TEXT,
        widen_atr_mult   REAL,
        profit_target_r  REAL,
        applied          INTEGER NOT NULL DEFAULT 0,
        pnl_r_at_decision REAL,
        pnl_r_truth      REAL,
        mark_source      TEXT,
        mark_age_ms      INTEGER,
        exit_state       TEXT,
        -- outcome cols (NULL until the close backfill fills them)
        realized_pnl_r   REAL,
        close_reason     TEXT,
        mfe_r_final      REAL,
        mae_r_final      REAL,
        giveback_r       REAL,
        time_to_exit_sec INTEGER,
        outcome_ts       INTEGER
    );
    """,
    # Partial index over the still-open decisions the close backfill targets.
    """
    CREATE INDEX IF NOT EXISTS idx_probe_decisions_open
        ON probe_decisions(position_id) WHERE realized_pnl_r IS NULL;
    """,
    # Calibration view: each decision joined to its realized outcome.
    """
    CREATE VIEW IF NOT EXISTS v_probe_outcomes AS
    SELECT decision_id, ts, run_id, position_id, mode, composite_lean, action,
           applied, pnl_r_at_decision, pnl_r_truth, exit_state,
           realized_pnl_r, close_reason, mfe_r_final, mae_r_final, giveback_r,
           time_to_exit_sec, outcome_ts
    FROM probe_decisions
    WHERE realized_pnl_r IS NOT NULL;
    """,
)


def open_probe_db(path: str | Path) -> sqlite3.Connection:
    """Open (and bootstrap) the SEPARATE probe tuning-log sidecar DB.

    Mirrors ``open_sentinel_db``: autocommit, its own file, never the live DB.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, isolation_level=None, timeout=10.0)
    for stmt in PROBE_DDL:
        conn.execute(stmt)
    return conn


def log_probe_readings(
    conn: sqlite3.Connection | None,
    *,
    ts: int,
    gate_id: int,
    position_id: str,
    readings: list[ProbeReading],
) -> None:
    """Append one ``probe_readings`` row per reading (FAIL-OPEN).

    No-op when ``conn`` is None / the table is absent / any sqlite error — the
    live tick must never crash on a log failure (cloned from ``shadow_log``).
    """
    if conn is None or not readings:
        return
    with contextlib.suppress(sqlite3.Error):
        conn.executemany(
            "INSERT INTO probe_readings "
            "(reading_id, ts, gate_id, position_id, probe_id, kind, lean, "
            " confidence, evidence_json) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    uuid.uuid4().hex, int(ts), int(gate_id), str(position_id),
                    r.probe_id, r.kind, float(r.lean), float(r.confidence),
                    json.dumps(r.evidence, sort_keys=True, default=str),
                )
                for r in readings
            ],
        )


def log_probe_decisions(
    conn: sqlite3.Connection | None,
    *,
    ts: int,
    run_id: str,
    position_id: str,
    mode: str,
    decision: EngineDecision,
    pnl_r_at_decision: float,
    pnl_r_truth: float,
    mark_source: str,
    mark_age_ms: int,
    exit_state: str,
    eval_id: str | None = None,
) -> None:
    """Append one ``probe_decisions`` row (FAIL-OPEN, outcome cols NULL).

    No-op when ``conn`` is None / the table is absent / any sqlite error.
    ``applied`` is persisted as INT (False → 0) — in observe mode it is always 0.
    """
    if conn is None:
        return
    mfe_protect_json = (
        None if decision.mfe_protect is None
        else json.dumps(decision.mfe_protect, sort_keys=True)
    )
    with contextlib.suppress(sqlite3.Error):
        conn.execute(
            "INSERT INTO probe_decisions "
            "(decision_id, eval_id, ts, run_id, position_id, mode, "
            " composite_lean, action, trail_mult, mfe_protect_json, "
            " widen_atr_mult, profit_target_r, applied, pnl_r_at_decision, "
            " pnl_r_truth, mark_source, mark_age_ms, exit_state) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                eval_id if eval_id is not None else uuid.uuid4().hex,
                int(ts), str(run_id), str(position_id), str(mode),
                float(decision.composite_lean), str(decision.action),
                decision.trail_mult, mfe_protect_json, decision.widen_atr_mult,
                decision.profit_target_r, 1 if decision.applied else 0,
                float(pnl_r_at_decision), float(pnl_r_truth), str(mark_source),
                int(mark_age_ms), str(exit_state),
            ),
        )


def backfill_probe_outcome(
    conn: sqlite3.Connection | None,
    *,
    position_id: str,
    realized_pnl_r: float,
    close_reason: str,
    mfe_r_final: float,
    mae_r_final: float,
    giveback_r: float,
    time_to_exit_sec: int,
    outcome_ts: int,
) -> None:
    """Fill the outcome cols on every still-open decision for ``position_id``.

    FAIL-OPEN (mirrors ``_safe_record_meta_label``): a missing table / any
    sqlite error is swallowed — a label failure must never abort an already
    committed close. Targets only rows whose ``realized_pnl_r IS NULL`` (the
    partial index) so a re-run / reopened id is idempotent.
    """
    if conn is None:
        return
    with contextlib.suppress(sqlite3.Error):
        conn.execute(
            "UPDATE probe_decisions SET realized_pnl_r=?, close_reason=?, "
            "mfe_r_final=?, mae_r_final=?, giveback_r=?, time_to_exit_sec=?, "
            "outcome_ts=? WHERE position_id=? AND realized_pnl_r IS NULL",
            (
                float(realized_pnl_r), str(close_reason), float(mfe_r_final),
                float(mae_r_final), float(giveback_r), int(time_to_exit_sec),
                int(outcome_ts), str(position_id),
            ),
        )
