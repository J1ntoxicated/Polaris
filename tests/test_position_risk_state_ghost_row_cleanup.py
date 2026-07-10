"""Ghost-row cleanup — every terminal-transition path must drop the sizer's
``position_risk_state`` row (2026-07-10 Capital exposure-starvation RCA).

Root cause: ``persist_position_risk_state`` writes an open-position risk row
that ONLY ``_close_trade_with_real_pnl`` (the normal close path) deleted.
FOUR other paths transition a position to a terminal status
(``closed``/``reconciled``) WITHOUT deleting the row — each is the SAME bug
class on a different venue/symbol path (Jin's "다른 심볼/베뉴 경로" sweep):

* ``_production_close._finalize_already_closed`` (idempotent-terminal dust)
* ``_production_close_helpers._reconcile_orphan`` (un-addressable orphan)
* ``reconcile_alpaca_zombies.reconcile_alpaca_zombies`` (dead-feed zombies)
* ``reconcile_alpaca_zombies.reconcile_alpaca_venue_drift`` (venue drift)
* ``correct_close_pnl_stamping.apply_corrections`` ``--fix-status`` branch
  (retroactive status correction)

Each left orphaned rows that permanently consumed per-symbol/cluster/track
sizing headroom (the Capital track-B 282.91%-used-vs-100%-cap incident: 56
ghost rows from reconciles/finalizes that never cleaned up). This file pins
the fix on every path found by that sweep.

DEMO/PAPER only; virtual funds. No real venue/network calls.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from polaris.core.data.position_risk_persist import persist_position_risk_state
from polaris.core.pipeline._sizer_payload import _read_portfolio_state
from polaris.scripts._production_close import _finalize_already_closed
from polaris.scripts._production_close_helpers import _reconcile_orphan
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.correct_close_pnl_stamping import analyze as analyze_close_stamps
from polaris.scripts.correct_close_pnl_stamping import apply_corrections
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.scripts.reconcile_alpaca_zombies import (
    reconcile_alpaca_venue_drift,
    reconcile_alpaca_zombies,
)
from polaris.storage.schema import init_db

NOW = 1_780_000_000


def _seed_open(
    conn: sqlite3.Connection, *, position_id: str, venue: str, symbol: str,
    strategy: str, opened_ts: int, status: str = "open",
) -> None:
    """A ``positions`` row + its paired ``position_risk_state`` row — the
    exact pairing every real open writes in one transaction."""
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, "
        "underlying_group_id, strategy_id, entry_strategy_id, "
        "active_strategy_id, side, qty, status, opened_ts, mae_r, risk_usd) "
        "VALUES (?, ?, ?, 'crypto:BTC', ?, ?, ?, 'long', 1.0, ?, ?, -0.2, 50.0)",
        (position_id, venue, symbol, strategy, strategy, strategy, status, opened_ts),
    )
    persist_position_risk_state(
        conn, venue=venue, symbol=symbol, instrument_id=f"{venue}:{symbol}",
        underlying_group_id="crypto:BTC", strategy=strategy, track="A",
        asset_class="crypto", signal_strength=0.8, notional_usd=20_000.0,
        equity_usd=79_000.0, opened_ts=opened_ts,
    )


def _assert_headroom_freed(conn: sqlite3.Connection) -> None:
    assert conn.execute(
        "SELECT COUNT(*) FROM position_risk_state"
    ).fetchone()[0] == 0
    ps = _read_portfolio_state(conn, equity_usd=79_000.0, track="A")
    assert ps.open_positions == []


def _trade(*, venue: str, symbol: str, strategy: str, position_id: str) -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue=venue, symbol=symbol,
        strategy_id=strategy, side="long", entry_price=50_000.0,
        notional_usd=20_000.0, open_ts=NOW, position_id=position_id,
    )
    t.base_qty = 0.4
    return t


def test_reconcile_orphan_frees_headroom(tmp_path: Path) -> None:
    """``_reconcile_orphan`` (un-addressable orphan, no real base to book)
    must delete the position_risk_state row on the SAME status='reconciled'
    transition."""
    conn = init_db(tmp_path / "p.sqlite")
    try:
        _seed_open(
            conn, position_id="pos1", venue="okx", symbol="BTC-USDT",
            strategy="volume_burst", opened_ts=NOW,
        )
        trade = _trade(venue="okx", symbol="BTC-USDT", strategy="volume_burst", position_id="pos1")
        state = ProdLoopState()
        state.open_trades = [trade]

        ok = _reconcile_orphan(
            conn, state=state, trade=trade, trade_idx=0, available=0.0, now_ts=NOW + 10,
        )

        assert ok is True
        row = conn.execute(
            "SELECT status FROM positions WHERE position_id='pos1'"
        ).fetchone()
        assert row[0] == "reconciled"
        _assert_headroom_freed(conn)
    finally:
        conn.close()


def test_finalize_already_closed_frees_headroom(tmp_path: Path) -> None:
    """The idempotent-terminal residual-dust path must also drop the row."""
    conn = init_db(tmp_path / "p.sqlite")
    try:
        _seed_open(
            conn, position_id="pos2", venue="okx", symbol="ETH-USDT",
            strategy="volume_burst", opened_ts=NOW,
        )
        trade = _trade(venue="okx", symbol="ETH-USDT", strategy="volume_burst", position_id="pos2")
        state = ProdLoopState()
        state.open_trades = [trade]

        ok = _finalize_already_closed(
            conn, state=state, trade=trade, trade_idx=0, now_ts=NOW + 10,
        )

        assert ok is True
        row = conn.execute(
            "SELECT status FROM positions WHERE position_id='pos2'"
        ).fetchone()
        assert row[0] == "closed"
        _assert_headroom_freed(conn)
    finally:
        conn.close()


def test_reconcile_alpaca_zombies_frees_headroom(tmp_path: Path) -> None:
    """Dead-feed zombie reconcile (unconditional, Alpaca-only) — same bug
    class, different venue path."""
    conn = init_db(tmp_path / "p.sqlite")
    try:
        _seed_open(
            conn, position_id="pos3", venue="alpaca", symbol="AAPL",
            strategy="equity_tsmom", opened_ts=NOW,
        )
        n = reconcile_alpaca_zombies(conn, now_ts=NOW + 10)
        assert n == 1
        row = conn.execute(
            "SELECT status FROM positions WHERE position_id='pos3'"
        ).fetchone()
        assert row[0] == "reconciled"
        _assert_headroom_freed(conn)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_reconcile_alpaca_venue_drift_frees_headroom(tmp_path: Path) -> None:
    """Venue-confirmed-absent drift reconcile — same bug class."""
    conn = init_db(tmp_path / "p.sqlite")
    try:
        _seed_open(
            conn, position_id="pos4", venue="alpaca", symbol="MSFT",
            strategy="equity_tsmom", opened_ts=NOW,
        )

        class _Adapter:
            async def fetch_positions(self) -> list[dict[str, Any]]:
                return []  # venue confirms MSFT absent

        n = await reconcile_alpaca_venue_drift(conn, _Adapter(), now_ts=NOW + 10)
        assert n == 1
        row = conn.execute(
            "SELECT status FROM positions WHERE position_id='pos4'"
        ).fetchone()
        assert row[0] == "reconciled"
        _assert_headroom_freed(conn)
    finally:
        conn.close()


def test_correct_close_pnl_stamping_fix_status_frees_headroom(tmp_path: Path) -> None:
    """The retroactive ``status='open'``→``'closed'`` correction (BG3-style
    one-off ops migration) must also drop the row — the exact live-DB
    signature this RCA traced back to (``correct_close_pnl_stamping.py``
    03:12 run leaving 56 Capital ghost rows behind)."""
    db = tmp_path / "p.sqlite"
    conn = init_db(db)
    try:
        _seed_open(
            conn, position_id="pos5", venue="capital", symbol="SG25",
            strategy="fx_breakout_basket", opened_ts=NOW,
        )
        # Fully-drained-open: a single close fill consumes the whole entry
        # qty while positions.status is stuck 'open' — the exact
        # ``fills-drained-open`` signature ``--fix-status`` targets.
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
            "side, size_usd, fill_price, fee_usd, slippage_bps, ts_ms, "
            "order_id, contribution_id, pnl_usd, is_close, base_qty, "
            "quote_qty, state) VALUES (?, 'capital', 'capital:SG25', "
            "'fx_breakout_basket', 'buy', 20000.0, 500.0, 0, 0, ?, '', "
            "'pos5', 0.0, 0, 0.4, 20000.0, 'filled')",
            (uuid.uuid4().hex, NOW * 1000),
        )
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, "
            "side, size_usd, fill_price, fee_usd, slippage_bps, ts_ms, "
            "order_id, contribution_id, pnl_usd, is_close, base_qty, "
            "quote_qty, state) VALUES (?, 'capital', 'capital:SG25', "
            "'fx_breakout_basket', 'sell', 20200.0, 505.0, 0, 0, ?, '', "
            "'pos5', 200.0, 1, 0.4, 20200.0, 'filled')",
            (uuid.uuid4().hex, NOW * 1000 + 1000),
        )
        conn.commit()

        reports = analyze_close_stamps(conn, tol_usd=0.01)
        rep = next(r for r in reports if r.position_id == "pos5")
        assert "fills-drained-open" in rep.flags

        n_pos, n_status = apply_corrections(
            conn, reports, fix_status=True, backup_path="", now_ts=NOW + 10,
        )
        assert n_status == 1
        row = conn.execute(
            "SELECT status FROM positions WHERE position_id='pos5'"
        ).fetchone()
        assert row[0] == "closed"
        _assert_headroom_freed(conn)
    finally:
        conn.close()
