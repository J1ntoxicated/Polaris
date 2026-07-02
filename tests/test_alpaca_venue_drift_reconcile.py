"""Alpaca ledger-venue re-sync — internal phantom cleanup (audit1 P0-4 ②).

DEMO/PAPER. 3 internal-only Alpaca positions (UPC/ABNB/SLS) sat
``status='open'`` in the DB with NO matching live venue position — the
position was closed/liquidated externally (a prior reset / demo auto-close /
manual close outside the bot) and the internal ledger never learned about it.
``reconcile_alpaca_venue_drift`` CHECKS the live venue book first and
reconciles ONLY the rows the venue confirms absent (a genuinely-still-open
row with a matching live venue position is left untouched — this is the
key difference from the unconditional ``reconcile_alpaca_zombies``).

Same Step M reconcile-as-drift-counter contract: ``status='reconciled'``,
``pnl_r`` LEFT NULL (excluded from R aggregation), ``exit_state='reconciled'``,
a drift note in ``risk_events``. NO fill is fabricated. flow_not_block.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from polaris.scripts.reconcile_alpaca_zombies import reconcile_alpaca_venue_drift
from polaris.storage.schema import init_db


class _MockAlpaca:
    def __init__(self, positions: list[dict[str, Any]]) -> None:
        self._positions = positions
        self.raise_on_fetch = False

    async def fetch_positions(self) -> list[dict[str, Any]]:
        if self.raise_on_fetch:
            raise RuntimeError("transport down")
        return self._positions


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
    c = init_db(tmp_path / "drift.sqlite")
    yield c
    c.close()


@pytest.mark.asyncio
async def test_reconciles_phantom_positions_absent_from_venue(
    conn: sqlite3.Connection,
) -> None:
    for pid, sym in (("p1", "UPC"), ("p2", "ABNB"), ("p3", "SLS")):
        _insert_position(conn, position_id=pid, venue="alpaca", symbol=sym,
                         mae_r=-0.6, risk_usd=40.0)
    adapter = _MockAlpaca([])  # venue holds NOTHING — all 3 are phantom drift
    n = await reconcile_alpaca_venue_drift(conn, adapter)
    assert n == 3
    rows = conn.execute(
        "SELECT status, exit_state, pnl_r FROM positions WHERE venue = 'alpaca'"
    ).fetchall()
    assert len(rows) == 3
    for status, exit_state, pnl_r in rows:
        assert status == "reconciled"
        assert exit_state == "reconciled"
        assert pnl_r is None


@pytest.mark.asyncio
async def test_live_venue_match_is_left_untouched(
    conn: sqlite3.Connection,
) -> None:
    # BB is genuinely still open (venue confirms it) — must NOT be reconciled.
    # UPC has no venue match — phantom drift, reconciled.
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="BB")
    _insert_position(conn, position_id="p2", venue="alpaca", symbol="UPC")
    adapter = _MockAlpaca([{"symbol": "BB", "qty": "50"}])
    n = await reconcile_alpaca_venue_drift(conn, adapter)
    assert n == 1
    bb_status = conn.execute(
        "SELECT status FROM positions WHERE position_id = 'p1'"
    ).fetchone()[0]
    upc_status = conn.execute(
        "SELECT status FROM positions WHERE position_id = 'p2'"
    ).fetchone()[0]
    assert bb_status == "open", "live venue match must be left untouched"
    assert upc_status == "reconciled"


@pytest.mark.asyncio
async def test_does_not_fabricate_fills(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="UPC")
    adapter = _MockAlpaca([])
    await reconcile_alpaca_venue_drift(conn, adapter)
    fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    assert fills == 0


@pytest.mark.asyncio
async def test_writes_drift_note_per_phantom(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="ABNB",
                     mae_r=-1.2, risk_usd=30.0)
    adapter = _MockAlpaca([])
    await reconcile_alpaca_venue_drift(conn, adapter)
    rows = conn.execute(
        "SELECT event_type, payload_json FROM risk_events "
        "WHERE event_type = 'alpaca_venue_drift'"
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0][1])
    assert payload["venue"] == "alpaca"
    assert payload["symbol"] == "ABNB"
    assert payload["est_drift_usd"] == pytest.approx(-36.0)
    assert "reason" in payload


@pytest.mark.asyncio
async def test_only_alpaca_open_rows_considered(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="okx1", venue="okx", symbol="BTC-USDT")
    _insert_position(conn, position_id="cap1", venue="capital", symbol="XAUUSD")
    _insert_position(conn, position_id="alp1", venue="alpaca", symbol="SLS")
    adapter = _MockAlpaca([])
    n = await reconcile_alpaca_venue_drift(conn, adapter)
    assert n == 1
    okx = conn.execute(
        "SELECT status FROM positions WHERE position_id = 'okx1'"
    ).fetchone()[0]
    cap = conn.execute(
        "SELECT status FROM positions WHERE position_id = 'cap1'"
    ).fetchone()[0]
    assert okx == "open" and cap == "open"


@pytest.mark.asyncio
async def test_idempotent_second_run_is_noop(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="UPC",
                     mae_r=-0.5, risk_usd=40.0)
    adapter = _MockAlpaca([])
    assert await reconcile_alpaca_venue_drift(conn, adapter) == 1
    assert await reconcile_alpaca_venue_drift(conn, adapter) == 0
    cnt = conn.execute(
        "SELECT COUNT(*) FROM risk_events WHERE event_type = 'alpaca_venue_drift'"
    ).fetchone()[0]
    assert cnt == 1


@pytest.mark.asyncio
async def test_fetch_failure_skips_whole_pass_never_blind(
    conn: sqlite3.Connection,
) -> None:
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="UPC")
    adapter = _MockAlpaca([])
    adapter.raise_on_fetch = True
    n = await reconcile_alpaca_venue_drift(conn, adapter)
    assert n == 0
    status = conn.execute(
        "SELECT status FROM positions WHERE position_id = 'p1'"
    ).fetchone()[0]
    assert status == "open", "a venue read failure must never reconcile blind"


@pytest.mark.asyncio
async def test_no_phantom_rows_is_noop(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p1", venue="alpaca", symbol="BB")
    adapter = _MockAlpaca([{"symbol": "BB", "qty": "12"}])
    n = await reconcile_alpaca_venue_drift(conn, adapter)
    assert n == 0
