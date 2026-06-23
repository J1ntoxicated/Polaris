"""Alpaca zombie force-reconcile — unexitable dead-feed opens marked terminal.

Jin 2026-06-22 (coherence audit): the Alpaca feed went 98.7h dead, leaving 3
``status='open'`` Alpaca positions (CAST/GPUS/CRVO) that can never be exited
against the dead feed. This one-time, guarded path marks those unmanageable
opens TERMINAL so the exit engine + hydrate stop retrying them.

Consistent with the Step M reconcile-as-drift-counter redefinition
(``_reconcile_orphan``): a reconcile is a TRACKING FAILURE, NOT a trade —
``status='reconciled'``, ``pnl_r`` LEFT NULL (excluded from every R aggregation),
``exit_state='reconciled'``, and a drift note row in ``risk_events`` (a rough
DOLLAR drift estimate, never mixed into the R ledger). NO fill is fabricated.

Guard contract:
- ONLY Alpaca ``status='open'`` rows are touched (OKX/Capital untouched).
- Idempotent: a second run reconciles 0 (already-reconciled rows are skipped).
- pnl_r stays NULL on every reconciled row (R-ledger honesty).
- a ``zombie_reconciled`` drift note is written per reconciled position.

DEMO/PAPER only. flow_not_block: clearing dead zombies UNblocks the book.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from polaris.scripts.reconcile_alpaca_zombies import reconcile_alpaca_zombies
from polaris.storage.schema import init_db


def _insert_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str,
    symbol: str,
    status: str = "open",
    mae_r: float | None = None,
    risk_usd: float | None = None,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "mae_r, risk_usd, exit_state) "
        "VALUES (?, ?, ?, ?, ?, ?, 'long', 10.0, ?, ?, ?, ?, 'open')",
        (
            position_id, venue, symbol, "equity_tsmom", "equity_tsmom",
            "equity_tsmom", status, int(time.time()) - 100 * 3600,
            mae_r, risk_usd,
        ),
    )
    conn.commit()


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = init_db(tmp_path / "zombie.sqlite")
    yield c
    c.close()


def test_reconciles_the_three_alpaca_zombies(conn: sqlite3.Connection) -> None:
    for pid, sym in (("p1", "CAST"), ("p2", "GPUS"), ("p3", "CRVO")):
        _insert_position(conn, position_id=pid, venue="alpaca", symbol=sym,
                         mae_r=-0.8, risk_usd=50.0)
    n = reconcile_alpaca_zombies(conn)
    assert n == 3
    rows = conn.execute(
        "SELECT status, exit_state, pnl_r FROM positions WHERE venue = 'alpaca'"
    ).fetchall()
    assert len(rows) == 3
    for status, exit_state, pnl_r in rows:
        assert status == "reconciled"
        assert exit_state == "reconciled"
        assert pnl_r is None, "reconcile is a tracking failure — pnl_r MUST stay NULL"


def test_does_not_fabricate_fills(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="CAST",
                     mae_r=-0.8, risk_usd=50.0)
    reconcile_alpaca_zombies(conn)
    # No fill row should be fabricated by the reconcile (no close leg invented).
    fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    assert fills == 0, "reconcile must NOT fabricate a close fill"


def test_writes_drift_note_per_zombie(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="CAST",
                     mae_r=-1.0, risk_usd=50.0)
    reconcile_alpaca_zombies(conn)
    rows = conn.execute(
        "SELECT event_type, payload_json FROM risk_events "
        "WHERE event_type = 'zombie_reconciled'"
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0][1])
    assert payload["venue"] == "alpaca"
    assert payload["symbol"] == "CAST"
    # Rough dollar drift estimate = min(0, mae_r) * risk_usd = -1.0 * 50 = -50.
    assert payload["est_drift_usd"] == pytest.approx(-50.0)
    assert "reason" in payload


def test_only_alpaca_open_rows_touched(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="okx1", venue="okx", symbol="BTC-USDT")
    _insert_position(conn, position_id="cap1", venue="capital", symbol="XAUUSD")
    _insert_position(conn, position_id="alp1", venue="alpaca", symbol="CAST")
    n = reconcile_alpaca_zombies(conn)
    assert n == 1
    okx = conn.execute(
        "SELECT status FROM positions WHERE position_id = 'okx1'"
    ).fetchone()[0]
    cap = conn.execute(
        "SELECT status FROM positions WHERE position_id = 'cap1'"
    ).fetchone()[0]
    assert okx == "open" and cap == "open", "non-Alpaca venues must be untouched"


def test_idempotent_second_run_is_noop(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="CAST",
                     mae_r=-0.5, risk_usd=40.0)
    assert reconcile_alpaca_zombies(conn) == 1
    assert reconcile_alpaca_zombies(conn) == 0, "already-reconciled rows skipped"
    # Exactly one drift note (no duplicate on the second run).
    cnt = conn.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type = 'zombie_reconciled'"
    ).fetchone()[0]
    assert cnt == 1


def test_null_mae_or_risk_yields_zero_drift(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="GPUS",
                     mae_r=None, risk_usd=None)
    reconcile_alpaca_zombies(conn)
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM risk_events WHERE event_type = 'zombie_reconciled'"
    ).fetchone()[0])
    assert payload["est_drift_usd"] == 0.0
    # Still reconciled, pnl_r NULL.
    status, pnl_r = conn.execute(
        "SELECT status, pnl_r FROM positions WHERE position_id = 'p1'"
    ).fetchone()
    assert status == "reconciled" and pnl_r is None


def test_positive_mae_floors_drift_at_zero(conn: sqlite3.Connection) -> None:
    # est_drift_usd = min(0, mae_r) * risk_usd — a (degenerate) positive mae_r
    # must never produce a positive "drift" that looks like a gain.
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="CRVO",
                     mae_r=0.5, risk_usd=50.0)
    reconcile_alpaca_zombies(conn)
    payload = json.loads(conn.execute(
        "SELECT payload_json FROM risk_events WHERE event_type = 'zombie_reconciled'"
    ).fetchone()[0])
    assert payload["est_drift_usd"] == 0.0
