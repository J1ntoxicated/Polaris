"""backgate-plan W2-d (design-exit-matrix.md §A) — OFFLINE exit-parameter
counterfactual calibrator.

DEMO/PAPER. The calibrator is an OFFLINE/cron consumer over
``v_probe_outcomes``: it sweeps candidate harvest (+H) / protect-floor (-F) R
thresholds and counterfactually rescores each against the trade's OWN
``mfe_r_final`` / ``mae_r_final`` / ``realized_pnl_r``. NEVER imported by any
in-loop path and NEVER auto-applies a knob.

These tests prove: the harvest/protect counterfactual math (hand-computed),
the noise-floor gate, the forward reset filter, per-POSITION dedup (a closed
position carries MANY probe_decisions rows — one per observed tick — that all
share the SAME backfilled outcome triple), fail-open on conn=None, the digest
never claims an auto-apply, and the offline-only import boundary.
"""

from __future__ import annotations

import sqlite3

from polaris.core.probes import EngineDecision, ProbeReading
from polaris.core.probes.exit_calibrator import (
    ExitCalibrationResult,
    read_exit_calibration,
    render_exit_digest,
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
    realized_pnl_r: float,
    mfe_r_final: float,
    mae_r_final: float,
    n_decisions: int = 1,
) -> None:
    """Log ``n_decisions`` reading+decision rows (simulating N observed ticks)
    then backfill ONE outcome — mirrors ``backfill_probe_outcome``'s real
    behaviour of overwriting EVERY still-open decision row for the position.
    """
    for i in range(n_decisions):
        log_probe_readings(
            conn, ts=ts + i, gate_id=6, position_id=pid,
            readings=[ProbeReading("technical", "technical", 0.0, 0.5, {})],
        )
        log_probe_decisions(
            conn, ts=ts + i, run_id="r", position_id=pid, mode="observe",
            decision=EngineDecision(action="HOLD", composite_lean=0.0),
            pnl_r_at_decision=0.0, pnl_r_truth=0.0,
            mark_source="tick", mark_age_ms=0, exit_state="open",
        )
    backfill_probe_outcome(
        conn, position_id=pid, realized_pnl_r=realized_pnl_r,
        close_reason="atr_trail_stop", mfe_r_final=mfe_r_final,
        mae_r_final=mae_r_final, giveback_r=mfe_r_final - realized_pnl_r,
        time_to_exit_sec=300, outcome_ts=ts + 300,
    )


def _seed_uniform(conn: sqlite3.Connection, *, n: int, mfe: float, mae: float, real: float) -> None:
    for i in range(n):
        _seed_closed(
            conn, pid=f"p{i}", ts=2000 + i,
            realized_pnl_r=real, mfe_r_final=mfe, mae_r_final=mae,
        )


def test_harvest_counterfactual_below_and_above_mfe(tmp_path: object) -> None:
    """H below the reached MFE fires at H; H above it never fires (actual stands)."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    _seed_uniform(conn, n=25, mfe=0.5, mae=-0.1, real=0.3)
    res = read_exit_calibration(
        conn, reset_ts=0, harvest_candidates=(0.20, 0.60),
    )
    by_param = {s.param_r: s for s in res.harvest}
    below = by_param[0.20]
    above = by_param[0.60]
    assert below.n == 25
    assert abs(below.mean_actual_r - 0.3) < 1e-9
    assert abs(below.mean_counterfactual_r - 0.20) < 1e-9  # fires at H=0.20
    assert abs(below.delta_r - (0.20 - 0.3)) < 1e-9
    assert abs(above.mean_counterfactual_r - 0.3) < 1e-9  # H=0.60 never reached
    assert abs(above.delta_r) < 1e-9
    conn.close()


def test_protect_counterfactual_breach_and_worse_only(tmp_path: object) -> None:
    """F fires ONLY when MAE breached AND the actual exit was worse than -F."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    _seed_uniform(conn, n=25, mfe=0.1, mae=-0.6, real=-0.5)
    res = read_exit_calibration(
        conn, reset_ts=0, protect_candidates=(0.30, 0.50, 0.80),
    )
    by_param = {s.param_r: s for s in res.protect}
    # 0.30: mae -0.6 <= -0.30 (breach) AND real -0.5 < -0.30 (worse) → fires.
    breached = by_param[0.30]
    assert abs(breached.mean_counterfactual_r - (-0.30)) < 1e-9
    assert abs(breached.delta_r - (-0.30 - (-0.5))) < 1e-9
    # 0.50: breach (mae -0.6 <= -0.50) but real -0.5 is NOT < -0.50 (equal) →
    # the floor would not have improved on the actual exit → unaffected.
    boundary = by_param[0.50]
    assert abs(boundary.mean_counterfactual_r - (-0.5)) < 1e-9
    assert abs(boundary.delta_r) < 1e-9
    # 0.80: never breached (mae -0.6 > -0.80) → actual stands.
    unbreached = by_param[0.80]
    assert abs(unbreached.mean_counterfactual_r - (-0.5)) < 1e-9
    conn.close()


def test_dedup_by_position_multiple_decision_rows_do_not_inflate_n(
    tmp_path: object,
) -> None:
    """A closed position with MANY observed ticks contributes n=1, not n=ticks."""
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    for i in range(25):
        _seed_closed(
            conn, pid=f"multi{i}", ts=2000 + i * 10,
            realized_pnl_r=0.3, mfe_r_final=0.5, mae_r_final=-0.1,
            n_decisions=4,  # 4 observed ticks per position before close
        )
    raw_rows = conn.execute("SELECT COUNT(*) FROM v_probe_outcomes").fetchone()[0]
    assert raw_rows == 25 * 4  # the raw per-tick rows really are duplicated
    res = read_exit_calibration(conn, reset_ts=0)
    assert res.total_n == 25  # the calibrator dedupes to one row per position
    assert res.harvest[0].n == 25
    conn.close()


def test_forward_reset_filter_excludes_pre_reset(tmp_path: object) -> None:
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    for i in range(30):
        _seed_closed(
            conn, pid=f"old{i}", ts=1000 + i,
            realized_pnl_r=0.3, mfe_r_final=0.5, mae_r_final=-0.1,
        )
    for i in range(30):
        _seed_closed(
            conn, pid=f"new{i}", ts=5000 + i,
            realized_pnl_r=0.3, mfe_r_final=0.5, mae_r_final=-0.1,
        )
    res = read_exit_calibration(conn, reset_ts=5000)
    assert res.total_n == 30
    conn.close()


def test_below_min_n_reports_empty_sweep(tmp_path: object) -> None:
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    _seed_uniform(conn, n=5, mfe=0.5, mae=-0.1, real=0.3)  # below noise floor
    res = read_exit_calibration(conn, reset_ts=0)
    assert res.harvest == []
    assert res.protect == []
    assert res.total_n == 5
    conn.close()


def test_fail_open_conn_none() -> None:
    res = read_exit_calibration(None, reset_ts=0)
    assert isinstance(res, ExitCalibrationResult)
    assert res.harvest == []
    assert res.protect == []
    assert res.total_n == 0


def test_digest_renders_and_never_claims_auto_apply(tmp_path: object) -> None:
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    _seed_uniform(conn, n=25, mfe=0.5, mae=-0.1, real=0.3)
    res = read_exit_calibration(conn, reset_ts=0)
    digest = render_exit_digest(res)
    assert "excursion" in digest.lower()
    assert "never" in digest.lower() and "auto-appl" in digest.lower()
    assert "Harvest candidates" in digest
    assert "Protect-floor candidates" in digest
    conn.close()


def test_digest_states_noise_floor_when_thin(tmp_path: object) -> None:
    conn = open_probe_db(f"{tmp_path}/probes.sqlite")
    _seed_uniform(conn, n=3, mfe=0.5, mae=-0.1, real=0.3)
    res = read_exit_calibration(conn, reset_ts=0)
    digest = render_exit_digest(res)
    assert "noise floor" in digest.lower()
    conn.close()


def test_reader_not_imported_by_in_loop_path() -> None:
    """The exit calibrator is OFFLINE only — no in-loop module imports it."""
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
        if "probes.exit_calibrator" in text or "probes import exit_calibrator" in text:
            offenders.append(name)
    assert offenders == [], f"in-loop modules import the offline reader: {offenders}"
