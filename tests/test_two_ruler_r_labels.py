"""Hardening #6 (2026-06-23) — two-ruler R unit honesty.

Polaris carries TWO different R rulers that must never be silently mixed:

- the per-trade-ATR EXCURSION ruler (``mfe_r`` / ``mae_r`` via
  ``realised_r`` / ``_close_excursion_r``) — a per-trade stop-distance unit;
- the per-stream LEDGER ruler (``pnl_r`` / ``avg_r`` / ``sum_r`` via
  ``realised_r_stream``) — a cross-venue-comparable per-stream-R_budget unit.

This pins the unit at the seams:
- the active-position snapshot tile exposes excursion R as ``*_atr_r``
  (``mfe_atr_r`` / ``mae_atr_r``) so it is never confused with the ledger R;
- ``probe_decisions`` carries an explicit ``unit_tag`` column (every row, incl.
  backfilled, tagged ``'excursion'``) so the rank-4 calibration reader can never
  join excursion-R to stream-R without an explicit rescale.

Naming/telemetry only — zero behavior change, no R value altered.
"""

from __future__ import annotations

import dataclasses
import sqlite3

from polaris.core.probes.tuning_log import PROBE_DDL
from polaris.scripts.dashboard.snapshot_models import PositionRow


def test_position_row_excursion_is_atr_r_suffixed() -> None:
    """The active-position tile's excursion R fields are ``*_atr_r`` suffixed."""
    fields = {f.name for f in dataclasses.fields(PositionRow)}
    assert "mfe_atr_r" in fields
    assert "mae_atr_r" in fields
    # the un-suffixed (ambiguous) names are gone from the payload.
    assert "mfe_r" not in fields
    assert "mae_r" not in fields


def test_probe_decisions_has_unit_tag_excursion() -> None:
    """probe_decisions carries a unit_tag column defaulting to 'excursion'."""
    conn = sqlite3.connect(":memory:")
    for stmt in PROBE_DDL:
        conn.execute(stmt)
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(probe_decisions)")}
    assert "unit_tag" in cols

    # a row inserted WITHOUT specifying unit_tag defaults to 'excursion'
    # (the excursion ruler — mfe_r/mae_r/pnl_r in the probe path are all ATR-R).
    conn.execute(
        "INSERT INTO probe_decisions "
        "(decision_id, eval_id, ts, run_id, position_id, mode, composite_lean, "
        " action) VALUES ('d1','e1',1,'r1','p1','observe',0.0,'HOLD')",
    )
    tag = conn.execute(
        "SELECT unit_tag FROM probe_decisions WHERE decision_id='d1'"
    ).fetchone()[0]
    assert tag == "excursion"
    conn.close()
