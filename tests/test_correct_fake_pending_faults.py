"""Correction script for fake-PENDING Capital faults — dry-run/apply/idempotency.

DEMO/PAPER. The D-1 adapter bug labelled Capital HTTP errors "PENDING" →
FAULT_REJECT → SOFT_HALT chains. The script relabels those fault rows
(``reject`` → ``reject_invalidated`` + detail marker, NO deletion — A3 audit
trail) and invalidates the chained halts behind a recompute guard. All tests
run on a throwaway tmp-path DB; the live DB is never touched.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from polaris.scripts.correct_fake_pending_faults import run
from polaris.storage.schema import ALL_DDL

NOW = int(time.time())


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "polaris_fixture.sqlite"
    conn = sqlite3.connect(p, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()
    return p


def _seed_fault(
    conn: sqlite3.Connection,
    *,
    strategy: str,
    ts: int,
    reject_code: str = "PENDING",
    venue: str = "capital",
    phase: str = "real_open_fill",
    fault_type: str = "reject",
    symbol: str = "EURUSD",
) -> str:
    event_id = str(uuid.uuid4())
    detail = {"phase": phase, "venue": venue, "symbol": symbol, "reject_code": reject_code}
    conn.execute(
        "INSERT INTO strategy_fault_events "
        "(event_id, strategy_id, fault_type, event_ts, detail_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (event_id, strategy, fault_type, ts, json.dumps(detail, sort_keys=True)),
    )
    return event_id


def _seed_halt(
    conn: sqlite3.Connection,
    *,
    strategy: str,
    opened_ts: int,
    reject_code: str = "PENDING",
    reset_ts: int | None = None,
    reset_by: str | None = None,
    symbol: str = "EURUSD",
) -> str:
    halt_id = str(uuid.uuid4())
    detail = {
        "phase": "real_open_fill", "venue": "capital",
        "symbol": symbol, "reject_code": reject_code,
    }
    conn.execute(
        "INSERT INTO strategy_halts "
        "(halt_id, strategy_id, mode, reason_code, opened_ts, unblock_ts, "
        " reset_by, reset_ts, detail_json) "
        "VALUES (?, ?, 'SOFT_HALT', 'reject', ?, ?, ?, ?, ?)",
        (
            halt_id, strategy, opened_ts, opened_ts + 900,
            reset_by, reset_ts, json.dumps(detail, sort_keys=True),
        ),
    )
    return halt_id


def _seed_incident(db: Path) -> dict[str, Any]:
    """5 fake PENDING faults (2 strategies) + 2 chained halts (1 reset, 1 open)
    + 1 alpaca validation_rejected fault that must stay untouched."""
    conn = sqlite3.connect(db, isolation_level=None)
    fakes = [
        _seed_fault(conn, strategy="fx_breakout_basket", ts=NOW - 500),
        _seed_fault(conn, strategy="fx_breakout_basket", ts=NOW - 400),
        _seed_fault(conn, strategy="fx_breakout_basket", ts=NOW - 300),
        _seed_fault(conn, strategy="micro_reversion", ts=NOW - 200),
        _seed_fault(conn, strategy="micro_reversion", ts=NOW - 100),
    ]
    halt_reset = _seed_halt(
        conn, strategy="fx_breakout_basket", opened_ts=NOW - 300,
        reset_ts=NOW - 250, reset_by="auto",
    )
    halt_open = _seed_halt(conn, strategy="micro_reversion", opened_ts=NOW - 100)
    alpaca_fault = _seed_fault(
        conn, strategy="equity_tsmom", ts=NOW - 50,
        reject_code="validation_rejected", venue="alpaca", symbol="AAPL",
    )
    conn.close()
    return {
        "fakes": fakes, "halt_reset": halt_reset,
        "halt_open": halt_open, "alpaca_fault": alpaca_fault,
    }


def _fault_row(conn: sqlite3.Connection, event_id: str) -> tuple[str, dict[str, Any]]:
    r = conn.execute(
        "SELECT fault_type, detail_json FROM strategy_fault_events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    return str(r[0]), json.loads(r[1])


def _halt_row(conn: sqlite3.Connection, halt_id: str) -> dict[str, Any]:
    r = conn.execute(
        "SELECT reset_ts, reset_by, detail_json FROM strategy_halts WHERE halt_id = ?",
        (halt_id,),
    ).fetchone()
    return {"reset_ts": r[0], "reset_by": r[1], "detail": json.loads(r[2])}


def test_cleanup_dry_run_readonly(db_path: Path) -> None:
    seeded = _seed_incident(db_path)
    summary = run(str(db_path), apply=False, now_ts=NOW)
    assert summary["applied"] is False
    assert summary["fault_matches"] == 5
    assert summary["fault_by_strategy"] == {
        "fx_breakout_basket": 3, "micro_reversion": 2,
    }
    assert summary["halt_matches"] == 2
    assert summary["reverse_mixed_windows"] == 0
    # Dry-run made ZERO changes — ro URI connection cannot write.
    conn = sqlite3.connect(db_path, isolation_level=None)
    for event_id in seeded["fakes"]:
        ftype, detail = _fault_row(conn, event_id)
        assert ftype == "reject"
        assert "invalidated" not in detail
    for hid in (seeded["halt_reset"], seeded["halt_open"]):
        assert "invalidated" not in _halt_row(conn, hid)["detail"]
    conn.close()


def test_cleanup_apply_relabels_and_resets(db_path: Path) -> None:
    seeded = _seed_incident(db_path)
    summary = run(str(db_path), apply=True, now_ts=NOW)
    assert summary["applied"] is True
    assert summary["fault_relabeled"] == 5
    assert summary["halt_invalidated"] == 2
    assert summary["halt_reset"] == 1  # only the still-open halt is reset

    conn = sqlite3.connect(db_path, isolation_level=None)
    # Faults: relabeled + marker carries the original fault_type (restorable).
    for event_id in seeded["fakes"]:
        ftype, detail = _fault_row(conn, event_id)
        assert ftype == "reject_invalidated"
        assert detail["invalidated"]["orig_fault_type"] == "reject"
        assert detail["invalidated"]["reason"] == "fake_pending_http_error_mislabel"
    # Already-reset halt: marker only; reset fields untouched (behavior 0).
    h_reset = _halt_row(conn, seeded["halt_reset"])
    assert "invalidated" in h_reset["detail"]
    assert h_reset["reset_by"] == "auto"
    assert h_reset["reset_ts"] == NOW - 250
    # Open halt: marker + reset stamped by the correction.
    h_open = _halt_row(conn, seeded["halt_open"])
    assert "invalidated" in h_open["detail"]
    assert h_open["reset_by"] == "fake_pending_correction"
    assert h_open["reset_ts"] is not None
    # Alpaca row untouched.
    ftype, detail = _fault_row(conn, seeded["alpaca_fault"])
    assert ftype == "reject"
    assert "invalidated" not in detail
    conn.close()

    # Idempotency: a second --apply changes nothing.
    summary2 = run(str(db_path), apply=True, now_ts=NOW + 10)
    assert summary2["fault_matches"] == 0
    assert summary2["fault_relabeled"] == 0
    assert summary2["halt_matches"] == 0
    assert summary2["halt_invalidated"] == 0


def test_cleanup_recompute_guard(db_path: Path) -> None:
    """Mixed-window safety: a PENDING-triggered halt whose 600s window still
    holds >=3 REAL rejects after the relabel is preserved (STILL_VALID)."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    # Halt A — 2 fakes + 1 real in window → post-relabel 1 < 3 → INVALIDATED.
    _seed_fault(conn, strategy="s_a", ts=NOW - 590)
    _seed_fault(conn, strategy="s_a", ts=NOW - 300)
    _seed_fault(conn, strategy="s_a", ts=NOW - 10, reject_code="99999")
    halt_a = _seed_halt(conn, strategy="s_a", opened_ts=NOW)
    # Halt B — fake trigger but 3 REAL rejects in window → STILL_VALID.
    _seed_fault(conn, strategy="s_b", ts=NOW - 500, reject_code="99999")
    _seed_fault(conn, strategy="s_b", ts=NOW - 400, reject_code="99999")
    _seed_fault(conn, strategy="s_b", ts=NOW - 300, reject_code="99999")
    _seed_fault(conn, strategy="s_b", ts=NOW - 5)
    halt_b = _seed_halt(conn, strategy="s_b", opened_ts=NOW)
    conn.close()

    summary = run(str(db_path), apply=True, now_ts=NOW)
    verdicts = {h["halt_id"]: h["verdict"] for h in summary["halts"]}
    assert verdicts[halt_a] == "INVALIDATED"
    assert verdicts[halt_b] == "STILL_VALID"

    conn = sqlite3.connect(db_path, isolation_level=None)
    assert "invalidated" in _halt_row(conn, halt_a)["detail"]
    # STILL_VALID halt is preserved untouched (no marker, no reset).
    h_b = _halt_row(conn, halt_b)
    assert "invalidated" not in h_b["detail"]
    assert h_b["reset_ts"] is None
    conn.close()


def test_cleanup_reverse_mixed_window_reported(db_path: Path) -> None:
    """Review nit: a halt TRIGGERED by a non-PENDING reject whose window also
    held fake PENDINGs is out of scope for correction but must be REPORTED."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    _seed_fault(conn, strategy="s_c", ts=NOW - 200)  # fake in window
    _seed_fault(conn, strategy="s_c", ts=NOW - 100, reject_code="99999")
    halt_c = _seed_halt(conn, strategy="s_c", opened_ts=NOW, reject_code="99999")
    conn.close()

    summary = run(str(db_path), apply=False, now_ts=NOW)
    assert summary["reverse_mixed_windows"] == 1
    # The non-PENDING halt itself is NOT a correction target.
    assert halt_c not in {h["halt_id"] for h in summary["halts"]}
