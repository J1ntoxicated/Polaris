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
from polaris.core.probes.entrance import JudgeReading

__all__ = [
    "ENTRANCE_JUDGMENT_NEAR_THRESHOLD_BAND",
    "PROBE_DDL",
    "backfill_probe_outcome",
    "log_entrance_judgments",
    "log_probe_decisions",
    "log_probe_readings",
    "open_probe_db",
]

# A3 write-amplification cut (2026-07-07, forensic: 23.4M rows @ ~21/s, 75.6%
# trade_eligible=0 never-actionable): entrance_judgments is PURE OBSERVE-ONLY
# telemetry with NO runtime consumer (grepped — only tests + the deferred
# Increment-2 advisory reference it). Deep never-actionable rows (trade_eligible=0
# AND far from the trade floor) carry no calibration signal, so they are no
# longer persisted. Rows are KEPT when trade_eligible=1 OR the judge_margin
# (signed distance to the trade floor) is within this band — this preserves
# every borderline/calibration-relevant row (the ones a future floor-tuning pass
# would need) while dropping the deep-miss firehose. Widen (never hard-drop
# further) if a consumer needs more of the tail — this does NOT touch any
# live entry/exit/sizing decision, only what telemetry is written.
ENTRANCE_JUDGMENT_NEAR_THRESHOLD_BAND: float = 0.10

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
        -- regime — frontgate consumption SHADOW (backgate-plan W2-d, vault/
        -- 50_research/backgate-plan/design-exit-matrix.md §A). ``log_probe_
        -- decisions`` now writes ``ProbeContext.regime`` (구 4라벨) here for
        -- every caller that threads it (the production observe_probes attach
        -- does). NULL = legacy/un-stamped row (pre-fix rows, or a caller that
        -- omits the kwarg). PURE TELEMETRY: no runtime consumer reads this
        -- column. The OFFLINE calibrator (core/probes/calibration.py) does
        -- NOT read it yet this wave — it still hardcodes regime='unknown'
        -- (deferred split, W3) — so the producer/consumer wiring is only
        -- half-done; do not re-claim "unblocks the split" until W3 lands.
        regime           TEXT,
        -- Hardening #6 (2026-06-23): the R unit of every R column on this row.
        -- The probe path's pnl_r / mfe_r / mae_r AND the backfilled outcome R
        -- (realized_pnl_r / mfe_r_final / mae_r_final / giveback_r) are ALL the
        -- per-trade-ATR EXCURSION ruler — NEVER the per-stream ledger R. The
        -- rank-4 calibration reader must not join this to stream-R without an
        -- explicit rescale. DEFAULT tags legacy/backfilled rows too.
        unit_tag         TEXT NOT NULL DEFAULT 'excursion',
        -- AI-escalation observe-only seam (hardening #11, 2026-06-23). ambiguous
        -- = the composite landed in the HOLD dead band (between TIGHTEN and
        -- WIDEN thresholds) where a future arbiter could escalate. PURE
        -- TELEMETRY — the runtime action/knobs/applied are unchanged and GPT
        -- calls stay 0; the consumer (arbiter) is deferred (JIN-SURFACE rank
        -- 18). deadband_margin = signed distance to the nearer decisive
        -- threshold (negative = inside the band). quantizer_version stamps the
        -- threshold set so the offline reader never re-pools across a move.
        ambiguous        INTEGER NOT NULL DEFAULT 0,
        deadband_margin  REAL,
        quantizer_version TEXT,
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
    # Increment 1 — ENTRANCE ambiguity seam (2026-06-24). The EntranceJudge writes
    # one row per JUDGED candidate carrying the ambiguous flag + score + per-lens
    # leans (evidence_json). PURE TELEMETRY: NO AI call, NO runtime consumer — the
    # tick loop falls back to the deterministic ``trade_eligible`` default
    # (flow-preserving: an ambiguous symbol stays eligible). This table is the
    # explicit hand-off point for Increment 2's async-advisory consumer (deferred,
    # Jin confirm) which reads ``ambiguous=1`` rows OUT of the tick loop and posts
    # advisory, NEVER mutating eligibility synchronously. Mirrors probe_decisions:
    # ts-stamped (stale-advice TTL), its own sidecar (zero WAL contention).
    """
    CREATE TABLE IF NOT EXISTS entrance_judgments (
        judgment_id       TEXT PRIMARY KEY,
        ts                INTEGER NOT NULL,
        run_id            TEXT NOT NULL,
        cycle_ts          INTEGER NOT NULL,
        instrument_id     TEXT NOT NULL,
        venue             TEXT NOT NULL,
        symbol            TEXT NOT NULL,
        opportunity_score REAL NOT NULL,
        trade_eligible    INTEGER NOT NULL,
        ambiguous         INTEGER NOT NULL DEFAULT 0,
        judge_margin      REAL,
        contributing_json TEXT NOT NULL DEFAULT '[]',
        evidence_json     TEXT NOT NULL DEFAULT '{}'
    );
    """,
    # Partial index over the ambiguous rows the deferred Increment-2 advisory
    # consumer targets (reads ambiguous=1 OUT of the tick loop).
    """
    CREATE INDEX IF NOT EXISTS idx_entrance_judgments_ambiguous
        ON entrance_judgments(cycle_ts) WHERE ambiguous = 1;
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
    # Hardening #6 (2026-06-23): idempotent ADD COLUMN for an EXISTING probe DB
    # (CREATE TABLE IF NOT EXISTS never alters a pre-existing table). The
    # ``DEFAULT 'excursion'`` tags every pre-existing/backfilled row too. A
    # duplicate-column error (already migrated) is benign.
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute(
            "ALTER TABLE probe_decisions "
            "ADD COLUMN unit_tag TEXT NOT NULL DEFAULT 'excursion'"
        )
    # backgate-plan W2-d (vault/50_research/backgate-plan/design-exit-matrix.md
    # §A): idempotent ADD COLUMN for the regime frontgate-consumption SHADOW on
    # an EXISTING probe DB. NULL on legacy rows — PURE TELEMETRY, unblocks the
    # OFFLINE calibrator's regime split only (see calibration.py note).
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE probe_decisions ADD COLUMN regime TEXT")
    # Hardening #11 (2026-06-23): idempotent ADD COLUMN for the AI-escalation
    # observe-only seam on an EXISTING probe DB. ``ambiguous`` DEFAULT 0 leaves
    # legacy/backfilled rows un-flagged (not retro-escalated); deadband_margin /
    # quantizer_version are NULL on legacy rows. Duplicate-column = benign.
    for ddl in (
        "ALTER TABLE probe_decisions ADD COLUMN ambiguous INTEGER NOT NULL "
        "DEFAULT 0",
        "ALTER TABLE probe_decisions ADD COLUMN deadband_margin REAL",
        "ALTER TABLE probe_decisions ADD COLUMN quantizer_version TEXT",
    ):
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute(ddl)
    # The ambiguous calibration view is created HERE (after the ALTERs) so a
    # legacy DB has the ``ambiguous`` column before the view references it.
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute(
            "CREATE VIEW IF NOT EXISTS v_probe_ambiguous_outcomes AS "
            "SELECT decision_id, ts, run_id, position_id, mode, composite_lean, "
            "       action, deadband_margin, quantizer_version, applied, "
            "       pnl_r_at_decision, pnl_r_truth, exit_state, realized_pnl_r, "
            "       close_reason, mfe_r_final, mae_r_final, giveback_r, "
            "       time_to_exit_sec, outcome_ts "
            "FROM probe_decisions WHERE ambiguous = 1"
        )
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
    regime: str | None = None,
) -> None:
    """Append one ``probe_decisions`` row (FAIL-OPEN, outcome cols NULL).

    No-op when ``conn`` is None / the table is absent / any sqlite error.
    ``applied`` is persisted as INT (False → 0) — in observe mode it is always 0.
    ``regime`` (backgate-plan W2-d) is the caller's ``ProbeContext.regime`` at
    evaluation time; ``None`` (the default for un-stamped callers) persists as
    NULL — unchanged legacy semantics (see the DDL comment on this column).
    """
    if conn is None:
        return
    mfe_protect_json = (
        None if decision.mfe_protect is None
        else json.dumps(decision.mfe_protect, sort_keys=True)
    )
    # Hardening #11: stamp the quantizer version that produced this row's
    # ambiguous flag (local import keeps tuning_log free of an engine cycle).
    from polaris.core.probes.engine import QUANTIZER_VERSION  # noqa: PLC0415

    with contextlib.suppress(sqlite3.Error):
        conn.execute(
            "INSERT INTO probe_decisions "
            "(decision_id, eval_id, ts, run_id, position_id, mode, "
            " composite_lean, action, trail_mult, mfe_protect_json, "
            " widen_atr_mult, profit_target_r, applied, pnl_r_at_decision, "
            " pnl_r_truth, mark_source, mark_age_ms, exit_state, unit_tag, "
            " ambiguous, deadband_margin, quantizer_version, regime) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                eval_id if eval_id is not None else uuid.uuid4().hex,
                int(ts), str(run_id), str(position_id), str(mode),
                float(decision.composite_lean), str(decision.action),
                decision.trail_mult, mfe_protect_json, decision.widen_atr_mult,
                decision.profit_target_r, 1 if decision.applied else 0,
                float(pnl_r_at_decision), float(pnl_r_truth), str(mark_source),
                int(mark_age_ms), str(exit_state),
                # Hardening #6: probe R is the per-trade-ATR EXCURSION ruler.
                "excursion",
                # Hardening #11: observe-only escalation-seam telemetry.
                1 if decision.ambiguous else 0, decision.deadband_margin,
                QUANTIZER_VERSION,
                # backgate-plan W2-d: NULL when the caller has no regime yet.
                None if regime is None else str(regime),
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


def log_entrance_judgments(
    conn: sqlite3.Connection | None,
    *,
    ts: int,
    run_id: str,
    cycle_ts: int,
    readings: dict[str, JudgeReading],
    venue_symbol_by_iid: dict[str, tuple[str, str]] | None = None,
) -> None:
    """Append one ``entrance_judgments`` row per JudgeReading (FAIL-OPEN).

    PURE TELEMETRY for the ambiguity seam — NO AI call, NO runtime consumer (the
    deferred Increment-2 advisory reads it out of the tick loop). No-op when
    ``conn`` is None / the table is absent / any sqlite error (cloned from the
    probe writers — the focus pass must never crash on a log failure).
    ``venue_symbol_by_iid`` maps instrument_id → (venue, symbol); a missing entry
    falls back to splitting the instrument_id on ``:`` (the ``venue:symbol`` form).

    A3 write-amplification cut: a row is persisted only when ``trade_eligible``
    OR its ``judge_margin`` is within ``ENTRANCE_JUDGMENT_NEAR_THRESHOLD_BAND`` of
    the trade floor (calibration-relevant borderline). Deep never-actionable
    misses (trade_eligible=0, far below floor) are DROPPED before the INSERT —
    this only trims what telemetry is written, never the judgment itself (the
    in-memory ``readings`` — and thus focus rank / eligibility — is untouched).
    """
    if conn is None or not readings:
        return
    vs = venue_symbol_by_iid or {}
    rows = []
    for iid, r in readings.items():
        if not r.trade_eligible and abs(r.judge_margin) > ENTRANCE_JUDGMENT_NEAR_THRESHOLD_BAND:
            continue
        venue, symbol = vs.get(iid, _split_iid(iid))
        rows.append(
            (
                uuid.uuid4().hex, int(ts), str(run_id), int(cycle_ts), str(iid),
                venue, symbol, float(r.opportunity_score),
                1 if r.trade_eligible else 0, 1 if r.ambiguous else 0,
                float(r.judge_margin),
                json.dumps(r.contributing, sort_keys=True),
                json.dumps(r.evidence, sort_keys=True, default=str),
            )
        )
    if not rows:
        return
    with contextlib.suppress(sqlite3.Error):
        conn.executemany(
            "INSERT INTO entrance_judgments "
            "(judgment_id, ts, run_id, cycle_ts, instrument_id, venue, symbol, "
            " opportunity_score, trade_eligible, ambiguous, judge_margin, "
            " contributing_json, evidence_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def _split_iid(instrument_id: str) -> tuple[str, str]:
    """Split ``venue:symbol`` → (venue, symbol); ('', iid) when no separator."""
    if ":" in instrument_id:
        venue, symbol = instrument_id.split(":", 1)
        return venue, symbol
    return "", instrument_id
