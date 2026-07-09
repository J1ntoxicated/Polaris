"""EDGE-tab gate-kill-value panel wiring (07-08 BUILD) — /debate evidence only.

``DashboardSnapshot.gate_kill_value`` surfaces ``gate_kill_value.
compute_kill_value_hints`` (a SELECT-only aggregate over
``gate_kill_counterfactuals``) so the desktop board can show, per (gate_id ×
regime), whether G3/G4 is killing signals that would have WON. Display + the
``/debate`` evidence surface ONLY — never wired to a live gate threshold.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_models import GateKillValuePanel
from polaris.storage.schema import ALL_DDL

NOW = 1_780_000_000


def _mkdb(tmp_path: Path) -> Path:
    db_path = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()
    return db_path


def _seed_row(
    db_path: Path, *, event_id: str, decision: str, fwd_r_24h: float,
) -> None:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute(
        "INSERT INTO gate_kill_counterfactuals "
        "(event_id, run_id, signal_id, gate_id, decision, venue, symbol, "
        " strategy_id, side, regime, reason, model_used, decision_ts, "
        " mark_price, mark_ts, mark_source, atr_pct, atr_usd, cost_r, "
        " fwd_r_24h, created_ts) "
        "VALUES (?, ?, ?, 3, ?, 'okx', 'BTC-USDT', 'tsmom', 'long', 'chop', "
        " 'r', 'gpt', ?, 100.0, ?, 'bar_close:1H', 0.01, 2.0, 0.0, ?, ?)",
        (event_id, f"run-{event_id}", f"sig-{event_id}", decision, NOW, NOW,
         fwd_r_24h, NOW),
    )
    conn.close()


def test_gate_kill_value_absent_on_missing_db(tmp_path: Path) -> None:
    snap = collect_snapshot(tmp_path / "nope.sqlite")
    assert snap.gate_kill_value == GateKillValuePanel()
    assert snap.gate_kill_value.present is False


def test_gate_kill_value_graceful_zero_with_no_counterfactual_rows(
    tmp_path: Path,
) -> None:
    db_path = _mkdb(tmp_path)
    snap = collect_snapshot(db_path)
    assert snap.gate_kill_value.present is False
    assert snap.gate_kill_value.rows == []


def test_gate_kill_value_surfaces_anti_edge_cohort(tmp_path: Path) -> None:
    db_path = _mkdb(tmp_path)
    for i in range(5):
        _seed_row(db_path, event_id=f"k{i}", decision="KILL", fwd_r_24h=1.0)
    for i in range(5):
        _seed_row(db_path, event_id=f"p{i}", decision="PASS", fwd_r_24h=0.0)
    snap = collect_snapshot(db_path)
    panel = snap.gate_kill_value
    assert panel.present is True
    assert panel.auto_apply is False
    assert len(panel.rows) == 1
    row = panel.rows[0]
    assert row.gate_id == 3
    assert row.cohort == "chop"
    assert row.anti_edge is True
    assert row.n_killed == 5 and row.n_passed == 5
