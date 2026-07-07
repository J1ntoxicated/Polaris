"""Per-exchange VIRTUAL account equity derivation — TDD spec (Jin 2026-07-07).

DEMO/PAPER, VIRTUAL ACCOUNT. Zero venue calls — derives equity purely from the
internal ``fills``/``positions`` ledger. Pins:

  1. NO FILLS: equity == seed_equity exactly.
  2. REALIZED FOLDS IN: closed fills (net of fees) move equity from seed.
  3. RECONCILED EXCLUDED: a fill on a 'reconciled' position is excluded (same
     convention as the dashboard).
  4. RUIN RE-ANCHOR: after a ruin event, equity is derived from the
     ``reseeded_to`` anchor forward — pre-ruin losses are not double-counted.
  5. PER-VENUE ISOLATION: one venue's fills never bleed into another's.
"""

from __future__ import annotations

import sqlite3

from polaris.storage.virtual_account_equity import virtual_equity_now


def _seed_fill(
    conn: sqlite3.Connection,
    *,
    venue: str,
    pnl_usd: float,
    fee_usd: float,
    ts_ms: int,
    is_close: int = 1,
    contribution_id: str = "",
    fill_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " is_close, pnl_usd, contribution_id) "
        "VALUES (?, ?, ?, ?, 'buy', 100.0, 1.0, ?, 0.0, ?, ?, ?, ?, ?)",
        (
            fill_id or f"f_{ts_ms}_{venue}", venue, f"{venue}:BTC", "s",
            fee_usd, ts_ms, f"o_{ts_ms}", is_close, pnl_usd, contribution_id,
        ),
    )


def test_no_fills_equity_equals_seed(memdb: sqlite3.Connection) -> None:
    result = virtual_equity_now(memdb, exchange="okx", seed_equity=100_000.0)
    assert result.equity == 100_000.0
    assert result.realized_pnl_usd == 0.0
    assert result.unrealized_pnl_usd == 0.0


def test_realized_pnl_net_of_fees_folds_in(memdb: sqlite3.Connection) -> None:
    _seed_fill(memdb, venue="okx", pnl_usd=500.0, fee_usd=20.0, ts_ms=1_000_000)
    result = virtual_equity_now(memdb, exchange="okx", seed_equity=100_000.0)
    assert result.realized_pnl_usd == 480.0
    assert result.equity == 100_480.0


def test_reconciled_position_fill_excluded(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        "INSERT INTO positions (position_id, venue, symbol, underlying_group_id, "
        " strategy_id, entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts) VALUES ('p1', 'okx', 'BTC', 'crypto:BTC', 's', 's', 's', "
        " 'long', 1.0, 'reconciled', 900000)"
    )
    _seed_fill(
        memdb, venue="okx", pnl_usd=1000.0, fee_usd=5.0, ts_ms=1_000_000,
        contribution_id="p1",
    )
    result = virtual_equity_now(memdb, exchange="okx", seed_equity=100_000.0)
    assert result.realized_pnl_usd == 0.0
    assert result.equity == 100_000.0


def test_per_venue_isolation(memdb: sqlite3.Connection) -> None:
    _seed_fill(memdb, venue="okx", pnl_usd=500.0, fee_usd=0.0, ts_ms=1_000_000)
    _seed_fill(memdb, venue="capital", pnl_usd=-200.0, fee_usd=0.0, ts_ms=1_000_000)
    okx = virtual_equity_now(memdb, exchange="okx", seed_equity=100_000.0)
    cap = virtual_equity_now(memdb, exchange="capital", seed_equity=100_000.0)
    assert okx.equity == 100_500.0
    assert cap.equity == 99_800.0


def test_ruin_reanchor_excludes_pre_ruin_pnl(memdb: sqlite3.Connection) -> None:
    # Pre-ruin blowup: a big loss before the ruin ts.
    _seed_fill(memdb, venue="okx", pnl_usd=-60_000.0, fee_usd=0.0, ts_ms=1_000_000)
    memdb.execute(
        "INSERT INTO virtual_ruin_events "
        "(exchange, ruined_ts, low_equity, week_start_ts, reseeded_to) "
        "VALUES ('okx', 2000, 40000.0, 0, 100000.0)"
    )
    # Post-ruin fill (ts_ms=3,000,000 -> 3000s, after ruined_ts=2000s).
    _seed_fill(memdb, venue="okx", pnl_usd=300.0, fee_usd=0.0, ts_ms=3_000_000)
    result = virtual_equity_now(memdb, exchange="okx", seed_equity=100_000.0)
    # Anchored at reseeded_to=100000, only the POST-ruin +300 counts —
    # the pre-ruin -60000 is not double-counted.
    assert result.seed_anchor == 100_000.0
    assert result.realized_pnl_usd == 300.0
    assert result.equity == 100_300.0


def test_ruin_reanchor_uses_latest_ruin_when_multiple(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        "INSERT INTO virtual_ruin_events "
        "(exchange, ruined_ts, low_equity, week_start_ts, reseeded_to) "
        "VALUES ('okx', 1000, 40000.0, 0, 100000.0)"
    )
    memdb.execute(
        "INSERT INTO virtual_ruin_events "
        "(exchange, ruined_ts, low_equity, week_start_ts, reseeded_to) "
        "VALUES ('okx', 5000, 45000.0, 0, 100000.0)"
    )
    result = virtual_equity_now(memdb, exchange="okx", seed_equity=100_000.0)
    assert result.anchor_ts == 5000
