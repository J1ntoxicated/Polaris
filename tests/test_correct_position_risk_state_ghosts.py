"""Correction script — purge orphaned ``position_risk_state`` rows.

Fixture DB shapes (mirroring the traced live pollution, 2026-07-10 RCA):
* A live OPEN position with a correctly-paired ``position_risk_state`` row —
  must NOT be touched.
* A CLOSED position whose ``position_risk_state`` row was never deleted (the
  ``_reconcile_orphan`` / ``reconcile_alpaca_zombies`` / --fix-status gap) —
  must be purged.
* A ``position_risk_state`` row with NO matching ``positions`` row at all
  (LEFT JOIN edge case) — must also be purged.

Dry-run must leave the DB byte-identical; --apply is idempotent (2nd run = 0
corrections) and writes one aggregate ``risk_events`` audit row.

DEMO/PAPER only; virtual funds.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import pytest

from polaris.core.data.position_risk_persist import persist_position_risk_state
from polaris.scripts.correct_position_risk_state_ghosts import analyze, main
from polaris.storage.schema import init_db


def _pos(
    conn: sqlite3.Connection, *, pid: str, venue: str, symbol: str,
    strategy: str, status: str, opened_ts: int,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, strategy_id, entry_strategy_id, "
        "active_strategy_id, side, qty, status, opened_ts) "
        "VALUES (?, ?, ?, '', ?, ?, ?, 'long', 1.0, ?, ?)",
        (pid, venue, symbol, strategy, strategy, strategy, status, opened_ts),
    )


def _risk_row(
    conn: sqlite3.Connection, *, venue: str, symbol: str, strategy: str,
    opened_ts: int, notional: float = 20_000.0,
) -> None:
    persist_position_risk_state(
        conn, venue=venue, symbol=symbol, instrument_id=f"{venue}:{symbol}",
        underlying_group_id="crypto:BTC", strategy=strategy, track="A",
        asset_class="crypto", signal_strength=0.8, notional_usd=notional,
        equity_usd=79_000.0, opened_ts=opened_ts,
    )


def _seed_fixture(conn: sqlite3.Connection) -> None:
    # 1) live OPEN — correctly paired, must survive.
    _pos(conn, pid="p_live", venue="okx", symbol="BTC-USDT",
         strategy="volume_burst", status="open", opened_ts=1000)
    _risk_row(conn, venue="okx", symbol="BTC-USDT", strategy="volume_burst",
              opened_ts=1000)

    # 2) CLOSED but the risk row was never deleted (the ghost-row bug).
    _pos(conn, pid="p_ghost_closed", venue="capital", symbol="SG25",
         strategy="fx_breakout_basket", status="closed", opened_ts=2000)
    _risk_row(conn, venue="capital", symbol="SG25",
              strategy="fx_breakout_basket", opened_ts=2000, notional=32_400.0)

    # 3) RECONCILED but the risk row was never deleted.
    _pos(conn, pid="p_ghost_reconciled", venue="okx", symbol="ETH-USDT",
         strategy="volume_burst", status="reconciled", opened_ts=3000)
    _risk_row(conn, venue="okx", symbol="ETH-USDT", strategy="volume_burst",
              opened_ts=3000)

    # 4) no matching positions row at all (LEFT JOIN edge case).
    _risk_row(conn, venue="capital", symbol="US500", strategy="cci_reversion",
              opened_ts=4000)
    conn.commit()


@pytest.fixture
def db_path(tmp_path: Path) -> Iterable[Path]:
    path = tmp_path / "fixture.sqlite"
    conn = init_db(path)
    _seed_fixture(conn)
    conn.close()
    yield path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dry_run_leaves_db_byte_identical(db_path: Path) -> None:
    before = _sha(db_path)
    rc = main(["--db", str(db_path)])
    assert rc == 0
    assert _sha(db_path) == before
    assert not list(db_path.parent.glob("*.bak-*"))


def test_analyze_identifies_ghost_rows_only(db_path: Path) -> None:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        ghosts = analyze(conn)
    finally:
        conn.close()
    keys = {(g.venue, g.symbol, g.strategy, g.opened_ts) for g in ghosts}
    assert keys == {
        ("capital", "SG25", "fx_breakout_basket", 2000),
        ("okx", "ETH-USDT", "volume_burst", 3000),
        ("capital", "US500", "cci_reversion", 4000),
    }
    sg25 = next(g for g in ghosts if g.symbol == "SG25")
    assert sg25.matched_status == "closed"
    assert sg25.open_risk_pct == pytest.approx(32_400.0 / 79_000.0)
    us500 = next(g for g in ghosts if g.symbol == "US500")
    assert us500.matched_status is None


def test_apply_purges_ghosts_keeps_live_row(db_path: Path) -> None:
    rc = main(["--db", str(db_path), "--apply"])
    assert rc == 0
    assert list(db_path.parent.glob("*.bak-*"))
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT venue, symbol FROM position_risk_state"
        ).fetchall()
        assert rows == [("okx", "BTC-USDT")]
        n_audit = conn.execute(
            "SELECT COUNT(*) FROM risk_events "
            "WHERE event_type = 'position_risk_state_ghost_purge'"
        ).fetchone()[0]
        assert n_audit == 1
    finally:
        conn.close()


def test_apply_is_idempotent(db_path: Path) -> None:
    assert main(["--db", str(db_path), "--apply"]) == 0
    conn = sqlite3.connect(db_path)
    n1 = conn.execute("SELECT COUNT(*) FROM position_risk_state").fetchone()[0]
    conn.close()

    assert main(["--db", str(db_path), "--apply"]) == 0
    conn = sqlite3.connect(db_path)
    try:
        n2 = conn.execute("SELECT COUNT(*) FROM position_risk_state").fetchone()[0]
        assert n2 == n1
        assert analyze(conn) == []
        n_audit = conn.execute(
            "SELECT COUNT(*) FROM risk_events "
            "WHERE event_type = 'position_risk_state_ghost_purge'"
        ).fetchone()[0]
        assert n_audit == 1  # 2nd pass found 0 ghosts -> no new audit row
    finally:
        conn.close()
