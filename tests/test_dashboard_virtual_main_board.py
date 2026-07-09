"""Main-board VIRTUAL-ledger aggregates — TDD spec (Jin 2026-07-08 fix).

DEMO/PAPER, VIRTUAL ACCOUNT. Bug fix for the dashboard-live-net hunt: the
always-visible MAIN board (desktop header + mobile status strip) previously
showed ``daily_pnl_usd`` / ``session_pnl_usd`` / ``since_reset`` computed from
an UNFILTERED scan of the whole ``fills`` table — mixing the pre-virtual
real-roundtrip history in with the fresh $100k-per-exchange VIRTUAL ledger.
These tests pin the NEW ``virtual_daily_pnl_usd`` / ``virtual_session_pnl_usd``
/ ``virtual_since_reset`` aggregates (``snapshot_q_virtual``):

  1. PRE-ANCHOR (legacy, pre-virtual) fills for a venue are EXCLUDED.
  2. POST-ANCHOR (virtual sim) fills are INCLUDED.
  3. Aggregation sums correctly across the 3 registered venues.
  4. RECONCILIATION: ``virtual_session_pnl_usd`` totals to Σ per-venue
     ``virtual_account_equity.virtual_equity_now(...).realized_pnl_usd`` —
     the identical anchor mechanism, so the two never disagree.
  5. ``collect_snapshot`` end-to-end exposes the new fields.

Read-only display layer — no trading behavior, sizing, gating, or venue calls.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.scripts.dashboard.snapshot import collect_snapshot
from polaris.scripts.dashboard.snapshot_q_common import _aest_midnight_ms
from polaris.scripts.dashboard.snapshot_q_virtual import (
    virtual_daily_pnl_usd,
    virtual_session_pnl_usd,
    virtual_since_reset,
)
from polaris.storage.schema import init_db
from polaris.storage.virtual_account_equity import virtual_equity_now

NOW_S = 1783600000  # fixed literal ts — deterministic regardless of wall clock
_AEST_MIDNIGHT_MS = _aest_midnight_ms(NOW_S)
ANCHOR_TS = _AEST_MIDNIGHT_MS // 1000 + 3_600  # 1h after today's AEST midnight
PRE_ANCHOR_TS = ANCHOR_TS - 30 * 86_400  # 30 days before the anchor (legacy era)
POST_ANCHOR_TS = ANCHOR_TS + 1_800  # 30m after the anchor, still "today"


def _seed_fill(
    conn: sqlite3.Connection,
    *,
    fill_id: str,
    venue: str,
    pnl_usd: float,
    fee_usd: float,
    ts_s: int,
    is_close: int = 1,
    contribution_id: str = "",
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " is_close, pnl_usd, contribution_id) "
        "VALUES (?, ?, ?, 's', 'buy', 100.0, 1.0, ?, 0.0, ?, ?, ?, ?, ?)",
        (
            fill_id, venue, f"{venue}:BTC", fee_usd, ts_s * 1000,
            f"o_{fill_id}", is_close, pnl_usd, contribution_id,
        ),
    )


def _seed_ruin(
    conn: sqlite3.Connection, *, exchange: str, ruined_ts: int, reseeded_to: float = 100_000.0,
) -> None:
    conn.execute(
        "INSERT INTO virtual_ruin_events "
        "(exchange, ruined_ts, low_equity, week_start_ts, reseeded_to) "
        "VALUES (?, ?, 0.0, 0, ?)",
        (exchange, ruined_ts, reseeded_to),
    )


def _seed_closed_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str,
    opened_ts: int,
    close_pnl: float,
    close_fee: float,
    close_ts_s: int,
) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, closed_ts) VALUES (?, ?, 'BTC', 'crypto:BTC', 's', 's', 's', "
        " 'long', 1.0, 'closed', ?, ?)",
        (position_id, venue, opened_ts, close_ts_s),
    )
    _seed_fill(
        conn, fill_id=f"close_{position_id}", venue=venue, pnl_usd=close_pnl,
        fee_usd=close_fee, ts_s=close_ts_s, is_close=1, contribution_id=position_id,
    )


def test_virtual_daily_pnl_excludes_pre_anchor_legacy_fills(memdb: sqlite3.Connection) -> None:
    # Legacy (pre-virtual, 30 days before the anchor) real-roundtrip fill.
    _seed_fill(memdb, fill_id="legacy1", venue="okx", pnl_usd=-9_000.0, fee_usd=10.0, ts_s=PRE_ANCHOR_TS)
    _seed_ruin(memdb, exchange="okx", ruined_ts=ANCHOR_TS)
    # Fresh virtual-sim fill, today, after the anchor.
    _seed_fill(memdb, fill_id="virt1", venue="okx", pnl_usd=100.0, fee_usd=5.0, ts_s=POST_ANCHOR_TS)
    pnl, n = virtual_daily_pnl_usd(memdb, now_s=NOW_S)
    assert n == 1
    assert round(pnl, 6) == 95.0  # legacy -9000 excluded; only 100 - 5 counts


def test_virtual_session_pnl_excludes_pre_anchor_legacy_fills(memdb: sqlite3.Connection) -> None:
    _seed_fill(memdb, fill_id="legacy1", venue="capital", pnl_usd=-20_000.0, fee_usd=50.0, ts_s=PRE_ANCHOR_TS)
    _seed_ruin(memdb, exchange="capital", ruined_ts=ANCHOR_TS)
    _seed_fill(memdb, fill_id="virt1", venue="capital", pnl_usd=200.0, fee_usd=3.0, ts_s=POST_ANCHOR_TS)
    pnl, n = virtual_session_pnl_usd(memdb, now_s=NOW_S)
    assert n == 1
    assert round(pnl, 6) == 197.0


def test_virtual_daily_and_session_aggregate_across_venues(memdb: sqlite3.Connection) -> None:
    _seed_ruin(memdb, exchange="okx", ruined_ts=ANCHOR_TS)
    _seed_ruin(memdb, exchange="capital", ruined_ts=ANCHOR_TS)
    _seed_fill(memdb, fill_id="okx1", venue="okx", pnl_usd=50.0, fee_usd=1.0, ts_s=POST_ANCHOR_TS)
    _seed_fill(memdb, fill_id="cap1", venue="capital", pnl_usd=30.0, fee_usd=2.0, ts_s=POST_ANCHOR_TS)
    daily_pnl, daily_n = virtual_daily_pnl_usd(memdb, now_s=NOW_S)
    session_pnl, session_n = virtual_session_pnl_usd(memdb, now_s=NOW_S)
    assert daily_n == 2
    assert round(daily_pnl, 6) == 77.0  # (50-1) + (30-2)
    assert session_n == 2
    assert round(session_pnl, 6) == 77.0


def test_virtual_session_pnl_reconciles_to_virtual_equity_now(memdb: sqlite3.Connection) -> None:
    """The aggregate must total EXACTLY to Σ per-venue realized_pnl_usd from
    the SAME anchor mechanism virtual_account_equity uses — no double source."""
    _seed_ruin(memdb, exchange="okx", ruined_ts=ANCHOR_TS)
    _seed_fill(memdb, fill_id="legacy1", venue="okx", pnl_usd=-5_000.0, fee_usd=1.0, ts_s=PRE_ANCHOR_TS)
    _seed_fill(memdb, fill_id="virt1", venue="okx", pnl_usd=42.0, fee_usd=2.0, ts_s=POST_ANCHOR_TS)
    _seed_fill(memdb, fill_id="virt2", venue="capital", pnl_usd=-10.0, fee_usd=1.0, ts_s=POST_ANCHOR_TS)
    _seed_fill(memdb, fill_id="virt3", venue="alpaca", pnl_usd=5.0, fee_usd=0.0, ts_s=POST_ANCHOR_TS)

    session_pnl, _n = virtual_session_pnl_usd(memdb, now_s=NOW_S)
    expected = sum(
        virtual_equity_now(memdb, exchange=v, seed_equity=100_000.0).realized_pnl_usd
        for v in ("okx", "capital", "alpaca")
    )
    assert round(session_pnl, 6) == round(expected, 6)


def test_virtual_since_reset_excludes_pre_anchor_trade(memdb: sqlite3.Connection) -> None:
    _seed_ruin(memdb, exchange="okx", ruined_ts=ANCHOR_TS)
    # Closed BEFORE the anchor (legacy) — must be excluded.
    _seed_closed_position(
        memdb, position_id="legacy_p", venue="okx", opened_ts=PRE_ANCHOR_TS - 100,
        close_pnl=-1_000.0, close_fee=1.0, close_ts_s=PRE_ANCHOR_TS,
    )
    # Closed AFTER the anchor (virtual sim) — must be included.
    _seed_closed_position(
        memdb, position_id="virt_p", venue="okx", opened_ts=ANCHOR_TS + 10,
        close_pnl=100.0, close_fee=2.0, close_ts_s=POST_ANCHOR_TS,
    )
    rollup = virtual_since_reset(memdb)
    assert rollup.n == 1
    assert round(rollup.net_usd, 6) == 98.0
    assert rollup.win_pct == 100.0


def test_virtual_since_reset_aggregates_across_venues(memdb: sqlite3.Connection) -> None:
    _seed_ruin(memdb, exchange="okx", ruined_ts=ANCHOR_TS)
    _seed_ruin(memdb, exchange="capital", ruined_ts=ANCHOR_TS)
    _seed_closed_position(
        memdb, position_id="okx_p", venue="okx", opened_ts=ANCHOR_TS + 10,
        close_pnl=100.0, close_fee=2.0, close_ts_s=POST_ANCHOR_TS,
    )
    _seed_closed_position(
        memdb, position_id="cap_p", venue="capital", opened_ts=ANCHOR_TS + 10,
        close_pnl=-50.0, close_fee=1.0, close_ts_s=POST_ANCHOR_TS,
    )
    rollup = virtual_since_reset(memdb)
    assert rollup.n == 2
    assert round(rollup.net_usd, 6) == 47.0  # (100-2) + (-50-1)
    assert rollup.win_pct == 50.0


def test_virtual_since_reset_never_none_even_with_no_data(memdb: sqlite3.Connection) -> None:
    rollup = virtual_since_reset(memdb)
    assert rollup is not None
    assert rollup.n == 0
    assert rollup.net_usd == 0.0
    assert rollup.pf == 0.0


def test_collect_snapshot_exposes_virtual_main_board_fields(tmp_path: Path) -> None:
    """End-to-end via ``collect_snapshot`` (real wall-clock 'now', like the
    existing AEST-boundary tests — ``test_dashboard_today_aest_boundary.py``)
    so the 'today' floor lines up with what ``collect_snapshot`` itself reads."""
    import time

    now_s = int(time.time())
    anchor_ts = now_s - 3_600          # 1h ago — "today" AEST for any realistic
    pre_anchor_ts = now_s - 30 * 86_400  # 30 days ago — the legacy era
    post_anchor_ts = now_s - 60        # 1m ago — definitely after the anchor

    db_path = tmp_path / "polaris.sqlite"
    conn = init_db(db_path)
    _seed_ruin(conn, exchange="okx", ruined_ts=anchor_ts)
    _seed_fill(conn, fill_id="legacy1", venue="okx", pnl_usd=-9_000.0, fee_usd=1.0, ts_s=pre_anchor_ts)
    _seed_fill(conn, fill_id="virt1", venue="okx", pnl_usd=75.0, fee_usd=1.0, ts_s=post_anchor_ts)
    conn.commit()
    conn.close()

    snap = collect_snapshot(db_path)
    assert round(snap.virtual_session_pnl_usd, 6) == 74.0
    assert snap.virtual_session_trades == 1
    assert round(snap.virtual_daily_pnl_usd, 6) == 74.0
    assert snap.virtual_since_reset is not None
    # Reconciliation: the legacy global session_pnl_usd (unfiltered scan) must
    # still include the -9000 legacy fill — proving the two scopes genuinely
    # differ (this is the bug being fixed: they used to be the SAME number).
    assert snap.session_pnl_usd < snap.virtual_session_pnl_usd
