"""P5 gap-b — ``position_risk_state`` persist-on-open / delete-on-close wire.

Invariant under test: an open position MUST populate the table the sizer's
``PortfolioState`` reads, so the per-symbol / underlying / cluster caps bind
(capital-efficiency / opportunity-cost ceiling — NOT a defensive throttle), and
a close MUST drop that row so only live open risk remains.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.data.position_risk_persist import (
    delete_position_risk_state,
    persist_position_risk_state,
)
from polaris.core.pipeline._sizer_payload import (
    _read_portfolio_state,
    _read_strategy_risk_state,
)
from polaris.core.sizing import (
    SignalIntent,
    compute_size,
)
from polaris.storage.schema import init_db


def _open(conn: sqlite3.Connection, *, symbol: str = "BTC-USDT",
          underlying: str = "crypto:BTC", strategy: str = "volume_burst",
          notional: float = 20_000.0, equity: float = 79_000.0,
          opened_ts: int = 1000) -> None:
    # A real open always pairs a ``positions`` row (status='open') with the
    # ``position_risk_state`` row in the SAME transaction (see
    # ``_production_pipeline.py``) — ``_read_portfolio_state`` now JOINs on
    # this pairing (ghost-row RCA 2nd line of defense), so the fixture mirrors
    # it. INSERT OR REPLACE keyed on position_id so a same-PK re-open (the
    # idempotency test) stays a no-op double-insert, not a duplicate row.
    conn.execute(
        "INSERT OR REPLACE INTO positions (position_id, venue, symbol, "
        "underlying_group_id, strategy_id, entry_strategy_id, "
        "active_strategy_id, side, qty, status, opened_ts) "
        "VALUES (?, 'okx', ?, ?, ?, ?, ?, 'long', 1.0, 'open', ?)",
        (
            f"okx:{symbol}:{strategy}:{opened_ts}", symbol, underlying,
            strategy, strategy, strategy, opened_ts,
        ),
    )
    persist_position_risk_state(
        conn,
        venue="okx",
        symbol=symbol,
        instrument_id=f"okx:{symbol}",
        underlying_group_id=underlying,
        strategy=strategy,
        track="A",
        asset_class="crypto",
        signal_strength=0.8,
        notional_usd=notional,
        equity_usd=equity,
        opened_ts=opened_ts,
    )


def test_open_writes_one_row(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "p.sqlite")
    try:
        _open(conn)
        n = conn.execute("SELECT COUNT(*) FROM position_risk_state").fetchone()[0]
        assert n == 1
        row = conn.execute(
            "SELECT venue, symbol, strategy, open_risk_pct, notional_usd, "
            "cluster_id, track FROM position_risk_state"
        ).fetchone()
        assert row[0] == "okx" and row[1] == "BTC-USDT" and row[2] == "volume_burst"
        # open_risk_pct = notional / equity (the field every cap sums over).
        assert abs(row[3] - (20_000.0 / 79_000.0)) < 1e-9
        assert abs(row[4] - 20_000.0) < 1e-9
        # BTC underlying → BTC+ETH cluster resolved deterministically.
        assert row[5] == "crypto:BTC+ETH"
        assert row[6] == "A"
    finally:
        conn.close()


def test_portfolio_state_reflects_open_row(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "p.sqlite")
    try:
        _open(conn)
        ps = _read_portfolio_state(conn, equity_usd=79_000.0, track="A")
        assert len(ps.open_positions) == 1
        p = ps.open_positions[0]
        assert p.symbol == "BTC-USDT"
        assert p.cluster_id == "crypto:BTC+ETH"
        assert abs(p.open_risk_pct - (20_000.0 / 79_000.0)) < 1e-9
    finally:
        conn.close()


def _final_notional(conn: sqlite3.Connection, *, equity: float = 79_000.0) -> float:
    intent = SignalIntent(
        signal_id="sig1",
        venue="okx",
        symbol="BTC-USDT",
        instrument_id="okx:BTC-USDT",
        underlying_group_id="BTC",
        asset_class="crypto",
        strategy="volume_burst",
        track="A",
        regime="trend",
        direction="long",
        signal_strength=0.9,
        listing_age_hours=10_000.0,
        leverage=1.0,
        base_risk_pct=0.02,
    )
    risk_state = _read_strategy_risk_state(
        conn, venue="okx", strategy="volume_burst", now_ts=2000
    )
    portfolio = _read_portfolio_state(conn, equity_usd=equity, track="A")
    return compute_size(
        conn, intent=intent, risk_state=risk_state, portfolio=portfolio,
        now_ts=2000,
    ).final_notional_usd


def test_open_position_reduces_compute_size_headroom(tmp_path: Path) -> None:
    """Caps bind: a large open BTC position must reduce the sizer's headroom
    for the next BTC entry vs the empty-book baseline."""
    conn = init_db(tmp_path / "p.sqlite")
    try:
        baseline = _final_notional(conn)
        assert baseline > 0.0
        # The per-symbol / cluster caps are deliberately loose (99% — aggressive
        # ceiling, NOT a defensive throttle). Near-saturate the same-symbol cap
        # so the remaining headroom < the proposed entry; the cap then clips.
        _open(conn, notional=0.985 * 79_000.0, opened_ts=1000)
        capped = _final_notional(conn)
        assert capped < baseline
    finally:
        conn.close()


def test_close_deletes_row(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "p.sqlite")
    try:
        _open(conn, opened_ts=1234)
        assert conn.execute(
            "SELECT COUNT(*) FROM position_risk_state"
        ).fetchone()[0] == 1
        delete_position_risk_state(
            conn, venue="okx", symbol="BTC-USDT",
            strategy="volume_burst", opened_ts=1234,
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM position_risk_state"
        ).fetchone()[0] == 0
        ps = _read_portfolio_state(conn, equity_usd=79_000.0, track="A")
        assert ps.open_positions == []
    finally:
        conn.close()


def test_close_deletes_only_matching_position(tmp_path: Path) -> None:
    """A second concurrent open of the same name at a different opened_ts must
    survive the close of the first (PK-scoped delete)."""
    conn = init_db(tmp_path / "p.sqlite")
    try:
        _open(conn, opened_ts=1000)
        _open(conn, opened_ts=2000)
        delete_position_risk_state(
            conn, venue="okx", symbol="BTC-USDT",
            strategy="volume_burst", opened_ts=1000,
        )
        rows = conn.execute(
            "SELECT opened_ts FROM position_risk_state"
        ).fetchall()
        assert [r[0] for r in rows] == [2000]
    finally:
        conn.close()


def test_reopen_idempotent_no_double_count(tmp_path: Path) -> None:
    """Re-driving the same open (same PK) must not double-count open risk."""
    conn = init_db(tmp_path / "p.sqlite")
    try:
        _open(conn, opened_ts=5000)
        _open(conn, opened_ts=5000)
        n = conn.execute("SELECT COUNT(*) FROM position_risk_state").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def _seed_position_row(
    conn: sqlite3.Connection, *, venue: str, symbol: str, strategy: str,
    opened_ts: int, status: str,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        "strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        "opened_ts) VALUES (?, ?, ?, 'crypto:BTC', ?, ?, ?, 'long', 1.0, ?, ?)",
        (
            f"{venue}:{symbol}:{opened_ts}", venue, symbol, strategy, strategy,
            strategy, status, opened_ts,
        ),
    )


def test_portfolio_state_filters_ghost_row_via_positions_join(tmp_path: Path) -> None:
    """2026-07-10 ghost-row RCA (2nd line of defense): a ``position_risk_state``
    row whose matching ``positions`` row is no longer ``status='open'`` (any
    terminal-transition path that forgot to delete it) must NOT count toward
    the sizer's headroom, even if the primary delete-on-terminal fix is missed
    somewhere."""
    conn = init_db(tmp_path / "p.sqlite")
    try:
        persist_position_risk_state(
            conn, venue="okx", symbol="BTC-USDT", instrument_id="okx:BTC-USDT",
            underlying_group_id="crypto:BTC", strategy="volume_burst", track="A",
            asset_class="crypto", signal_strength=0.8, notional_usd=20_000.0,
            equity_usd=79_000.0, opened_ts=1000,
        )
        _seed_position_row(
            conn, venue="okx", symbol="BTC-USDT", strategy="volume_burst",
            opened_ts=1000, status="closed",
        )
        ps = _read_portfolio_state(conn, equity_usd=79_000.0, track="A")
        assert ps.open_positions == []
    finally:
        conn.close()


def test_portfolio_state_keeps_row_when_positions_status_open(tmp_path: Path) -> None:
    """The JOIN filter must not drop a genuinely live open position."""
    conn = init_db(tmp_path / "p.sqlite")
    try:
        persist_position_risk_state(
            conn, venue="okx", symbol="BTC-USDT", instrument_id="okx:BTC-USDT",
            underlying_group_id="crypto:BTC", strategy="volume_burst", track="A",
            asset_class="crypto", signal_strength=0.8, notional_usd=20_000.0,
            equity_usd=79_000.0, opened_ts=1000,
        )
        _seed_position_row(
            conn, venue="okx", symbol="BTC-USDT", strategy="volume_burst",
            opened_ts=1000, status="open",
        )
        ps = _read_portfolio_state(conn, equity_usd=79_000.0, track="A")
        assert len(ps.open_positions) == 1
        assert ps.open_positions[0].symbol == "BTC-USDT"
    finally:
        conn.close()
