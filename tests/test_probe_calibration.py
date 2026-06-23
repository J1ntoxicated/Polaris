"""ADR-011/012 — OFFLINE probe calibration reader (structural hardening #4).

DEMO/PAPER. The reader is an OFFLINE/cron consumer over ``v_probe_outcomes``: it
computes, per ``(kind, regime, action)``, the would-be (decision-time) vs
realized R / giveback separation, applies the ``measurement_resets`` forward
filter, and emits a Vault digest (+ optional ``probe_calibration`` table). It is
NEVER imported by any in-loop path and NEVER auto-applies a knob — separation
that is within noise reports ``insufficient separation, STAY observe``.

These tests prove: the SELECT/group/separation math, the forward reset filter,
the EXCURSION-unit guard (never join to stream-R), the insufficient-separation
STAY-observe verdict, and that the optional table write is fail-open.
"""

from __future__ import annotations

import sqlite3

from polaris.core.probes import EngineDecision, ProbeReading
from polaris.core.probes.calibration import (
    CalibrationResult,
    read_calibration,
    render_digest,
    write_calibration_table,
)
from polaris.core.probes.tuning_log import (
    backfill_probe_outcome,
    log_probe_decisions,
    log_probe_readings,
    open_probe_db,
)


def _seed_closed(
    conn: sqlite3.Connection,
    *,
    pid: str,
    ts: int,
    action: str,
    lean: float,
    kind: str,
    realized_pnl_r: float,
    mfe_r_final: float,
    giveback_r: float,
) -> None:
    """Log one reading+decision then backfill its realized outcome (closed)."""
    log_probe_readings(
        conn, ts=ts, gate_id=6, position_id=pid,
        readings=[ProbeReading(kind, kind, lean, 0.9, {})],  # type: ignore[arg-type]
    )
    log_probe_decisions(
        conn, ts=ts, run_id="r", position_id=pid, mode="observe",
        decision=EngineDecision(action=action, composite_lean=lean),  # type: ignore[arg-type]
        pnl_r_at_decision=lean, pnl_r_truth=lean,
        mark_source="tick", mark_age_ms=0, exit_state="open",
    )
    backfill_probe_outcome(
        conn, position_id=pid, realized_pnl_r=realized_pnl_r,
        close_reason="atr_trail_stop", mfe_r_final=mfe_r_final,
        mae_r_final=-0.2, giveback_r=giveback_r,
        time_to_exit_sec=300, outcome_ts=ts + 300,
    )


def test_separation_per_action_group(tmp_path: object) -> None:
    """A clear MFE-vs-realized giveback split per action surfaces as a group."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    # TIGHTEN group: high realized, low giveback (good let-run).
    for i in range(40):
        _seed_closed(
            conn, pid=f"t{i}", ts=2000 + i, action="TIGHTEN", lean=-0.3,
            kind="loss", realized_pnl_r=0.8, mfe_r_final=0.9, giveback_r=0.1,
        )
    # HARVEST group: low realized, high giveback (cut too early / gave back).
    for i in range(40):
        _seed_closed(
            conn, pid=f"h{i}", ts=2100 + i, action="HARVEST", lean=-0.6,
            kind="profit", realized_pnl_r=0.1, mfe_r_final=1.2, giveback_r=1.1,
        )
    res = read_calibration(conn, reset_ts=0)
    assert isinstance(res, CalibrationResult)
    groups = {(g.kind, g.action): g for g in res.groups}
    assert ("loss", "TIGHTEN") in groups
    assert ("profit", "HARVEST") in groups
    tighten = groups[("loss", "TIGHTEN")]
    harvest = groups[("profit", "HARVEST")]
    assert tighten.n == 40
    # would-be (mfe) minus realized = giveback separation per group.
    assert abs(tighten.mean_giveback_r - 0.1) < 1e-6
    assert abs(harvest.mean_giveback_r - 1.1) < 1e-6
    assert tighten.unit == "excursion"
    conn.close()


def test_forward_reset_filter_excludes_pre_reset(tmp_path: object) -> None:
    """Rows with ts < reset_ts are excluded from the calibration sample."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    for i in range(30):  # pre-reset (ts=1000..)
        _seed_closed(
            conn, pid=f"old{i}", ts=1000 + i, action="TIGHTEN", lean=-0.3,
            kind="loss", realized_pnl_r=0.5, mfe_r_final=0.6, giveback_r=0.1,
        )
    for i in range(30):  # post-reset (ts=5000..)
        _seed_closed(
            conn, pid=f"new{i}", ts=5000 + i, action="TIGHTEN", lean=-0.3,
            kind="loss", realized_pnl_r=0.5, mfe_r_final=0.6, giveback_r=0.1,
        )
    res = read_calibration(conn, reset_ts=5000)
    total = sum(g.n for g in res.groups)
    assert total == 30  # only post-reset rows survive the forward filter
    conn.close()


def test_insufficient_separation_stay_observe(tmp_path: object) -> None:
    """A thin / noisy sample yields the STAY-observe verdict (never auto-apply)."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    for i in range(3):  # below min-sample → noise
        _seed_closed(
            conn, pid=f"x{i}", ts=2000 + i, action="HOLD", lean=0.0,
            kind="technical", realized_pnl_r=0.0, mfe_r_final=0.1, giveback_r=0.1,
        )
    res = read_calibration(conn, reset_ts=0)
    assert res.verdict == "insufficient separation, STAY observe"
    digest = render_digest(res)
    assert "STAY observe" in digest
    assert "excursion" in digest.lower()
    conn.close()


def test_digest_renders_and_never_names_stream_r(tmp_path: object) -> None:
    """The digest labels the EXCURSION ruler and never claims stream-R."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    for i in range(40):
        _seed_closed(
            conn, pid=f"d{i}", ts=2000 + i, action="TIGHTEN", lean=-0.3,
            kind="loss", realized_pnl_r=0.8, mfe_r_final=0.9, giveback_r=0.1,
        )
    res = read_calibration(conn, reset_ts=0)
    digest = render_digest(res)
    assert "excursion" in digest.lower()
    assert "stream-r" not in digest.lower()
    assert "auto-appl" not in digest.lower() or "never" in digest.lower()
    conn.close()


def test_write_calibration_table_optional_and_fail_open(tmp_path: object) -> None:
    """The optional table write persists rows and is fail-open on missing DB."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    for i in range(40):
        _seed_closed(
            conn, pid=f"w{i}", ts=2000 + i, action="TIGHTEN", lean=-0.3,
            kind="loss", realized_pnl_r=0.8, mfe_r_final=0.9, giveback_r=0.1,
        )
    res = read_calibration(conn, reset_ts=0)
    write_calibration_table(conn, res)
    n = conn.execute("SELECT COUNT(*) FROM probe_calibration").fetchone()[0]
    assert n == len(res.groups)
    # fail-open: conn=None is a no-op, not a crash.
    write_calibration_table(None, res)
    conn.close()


def test_reader_not_imported_by_in_loop_path() -> None:
    """The calibration module is OFFLINE only — no in-loop module imports it."""
    import importlib.util
    import pkgutil

    import polaris.scripts as scripts_pkg

    offenders: list[str] = []
    for mod in pkgutil.iter_modules(scripts_pkg.__path__):
        name = f"polaris.scripts.{mod.name}"
        try:
            src = importlib.util.find_spec(name)
        except (ImportError, ValueError):  # pragma: no cover
            continue
        if src is None or src.origin is None:
            continue
        with open(src.origin, encoding="utf-8") as fh:
            text = fh.read()
        if "probes.calibration" in text or "probes import calibration" in text:
            offenders.append(name)
    assert offenders == [], f"in-loop modules import the offline reader: {offenders}"
