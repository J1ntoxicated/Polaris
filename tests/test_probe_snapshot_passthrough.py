"""ADR-012 Slice 1 — dashboard snapshot ``probe_events`` passthrough (fail-open).

DEMO/PAPER. A read-only, fail-open join of the most-recent probe readings +
decisions from the SEPARATE ``data/probes.sqlite`` sidecar, exposed as generic
``probe_events`` rows for the dashboard (which is separately wired to render
them). Empty list on a missing / locked / table-less sidecar — it must NEVER
break the snapshot.
"""

from __future__ import annotations

from pathlib import Path

from polaris.core.probes import EngineDecision, ProbeReading
from polaris.core.probes.tuning_log import (
    log_probe_decisions,
    log_probe_readings,
    open_probe_db,
)
from polaris.scripts.dashboard.snapshot import read_probe_events


def test_probe_events_empty_on_missing_db(tmp_path: Path) -> None:
    # No probes.sqlite present → empty list, no exception.
    assert read_probe_events(tmp_path / "does_not_exist.sqlite") == []


def test_probe_events_empty_on_tableless_db(tmp_path: Path) -> None:
    import sqlite3

    p = tmp_path / "empty.sqlite"
    sqlite3.connect(p).close()  # exists but has no probe tables
    assert read_probe_events(p) == []


def test_probe_events_returns_recent_rows(tmp_path: Path) -> None:
    p = tmp_path / "probes.sqlite"
    conn = open_probe_db(p)
    log_probe_readings(
        conn, ts=1000, gate_id=6, position_id="okx:BTC-USDT#1",
        readings=[
            ProbeReading(
                "profit_taking", "profit", -0.5, 0.8, {"giveback_r": 0.4}
            ),
        ],
    )
    log_probe_decisions(
        conn, ts=1000, run_id="r1", position_id="okx:BTC-USDT#1", mode="observe",
        decision=EngineDecision(
            action="HARVEST", composite_lean=-0.5, contributing=["profit_taking"]
        ),
        pnl_r_at_decision=0.3, pnl_r_truth=0.3, mark_source="ws",
        mark_age_ms=120, exit_state="open",
    )
    conn.close()

    events = read_probe_events(p)
    assert events  # non-empty
    ev = events[0]
    # Generic shape the dashboard consumes.
    for key in (
        "name", "ticker", "venue", "kind", "lean", "confidence", "reading",
        "action", "ts", "gate_id",
    ):
        assert key in ev, key
    # The reading row carries the probe id + lean; the decision carries action.
    names = {e["name"] for e in events}
    actions = {e.get("action") for e in events}
    assert "profit_taking" in names or "HARVEST" in actions


def test_probe_events_caps_recent_n(tmp_path: Path) -> None:
    p = tmp_path / "probes.sqlite"
    conn = open_probe_db(p)
    for i in range(50):
        log_probe_readings(
            conn, ts=1000 + i, gate_id=6, position_id=f"p{i}",
            readings=[ProbeReading("technical", "technical", 0.1, 0.3, {})],
        )
    conn.close()
    events = read_probe_events(p, limit=10)
    assert len(events) <= 10
