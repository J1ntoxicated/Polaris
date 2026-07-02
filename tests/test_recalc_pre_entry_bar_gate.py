"""[P0-5] ``load_active_position_rows`` must never feed PRE-ENTRY bars into
the peak/trough / MFE-MAE excursion tracker.

A reconcile-import (or any restart-hydrated) position can open AFTER the
last real bar close (e.g. an equity position imported minutes after the
session close). The pre-fix 20-bar lookback window had no lower bound, so it
reached back into stale market-closed (or simply pre-entry) bars — those
bars' high/low fabricated a peak/trough excursion the position never lived
through, and the entry-adjacent ``last_price`` could resolve to a STALE
close far from the true current price (a long position's derived STOP could
then sit ABOVE the honest current price).

Fix: the ``bars`` read in ``load_active_position_rows`` is bounded
``ts >= opened_ts`` (in addition to the existing ``ts <= now`` upper bound).
DEMO/PAPER only; virtual funds. Display/measurement-only — no size/entry
change, no throttle.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.scripts._production_recalc import load_active_position_rows

VENUE = "alpaca"
SYMBOL = "ABBV"
INSTRUMENT_ID = f"{VENUE}:{SYMBOL}"


def _seed_position(
    conn: sqlite3.Connection, *, position_id: str, opened_ts: int, entry_price: float,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, ?, ?, ?, '_reconcile_import', '_reconcile_import', "
        "        '_reconcile_import', 'long', 1.0, 'open', ?, 0)",
        (position_id, VENUE, SYMBOL, SYMBOL, opened_ts),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, '_reconcile_import', ?, ?, 'buy', 1.0, ?, ?, 0.0, 0.0, "
        "        0.0, 0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, opened_ts * 1000, INSTRUMENT_ID, VENUE,
            entry_price, entry_price, position_id, uuid.uuid4().hex,
        ),
    )


def _seed_bar(conn: sqlite3.Connection, *, ts: int, close: float, high: float, low: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO bars "
        "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
        " ts, open, high, low, close, volume, notional_usd, trade_count, "
        " vwap, bid_close, ask_close, spread_bps_close, source) "
        "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, 100.0, 0.0, 1, ?, ?, ?, 1.0, "
        "        'rest')",
        (INSTRUMENT_ID, SYMBOL, VENUE, SYMBOL, ts, close, high, low, close, close, close, close),
    )


def test_pre_entry_bars_excluded_from_last_price(memdb: sqlite3.Connection) -> None:
    """A position opened AFTER the last pre-entry bar must not read a stale
    pre-entry close as its current price — it degrades to the entry-price
    fallback (no post-entry bar has closed yet)."""
    opened_ts = 1_000_000
    entry_price = 100.0
    # Stale PRE-ENTRY bars (session-closed noise) with a wildly different
    # close/high/low — must be fully excluded from the window.
    for i in range(20):
        _seed_bar(
            memdb, ts=opened_ts - 3600 - i * 60,
            close=40.0, high=45.0, low=35.0,
        )
    _seed_position(memdb, position_id="pos1", opened_ts=opened_ts, entry_price=entry_price)

    rows = load_active_position_rows(memdb)
    assert len(rows) == 1
    pos = rows[0]
    # No post-entry bar exists yet → falls back to entry_price, NEVER the
    # pre-entry stale close (40.0).
    assert float(pos["last_price"]) == pytest.approx(entry_price)


def test_post_entry_bars_only_feed_excursion_window(memdb: sqlite3.Connection) -> None:
    """Post-entry bars (ts >= opened_ts) are read normally; pre-entry noise
    (ts < opened_ts) never leaks into the MFE/MAE-feeding last_price/atr_pct."""
    opened_ts = 1_000_000
    entry_price = 100.0
    # Pre-entry noise: extreme high/low that would fabricate a huge excursion
    # if it leaked into the window.
    for i in range(20):
        _seed_bar(
            memdb, ts=opened_ts - 3600 - i * 60,
            close=1000.0, high=2000.0, low=1.0,
        )
    # Post-entry bars: a modest, realistic move.
    for i in range(3):
        ts = opened_ts + i * 60
        close = entry_price + i * 0.5
        _seed_bar(memdb, ts=ts, close=close, high=close + 0.2, low=close - 0.2)
    _seed_position(memdb, position_id="pos1", opened_ts=opened_ts, entry_price=entry_price)

    rows = load_active_position_rows(memdb)
    assert len(rows) == 1
    pos = rows[0]
    # last_price = the newest POST-entry bar's close (101.0), not the
    # pre-entry noise (1000.0).
    assert float(pos["last_price"]) == pytest.approx(101.0)
    # atr_pct derived ONLY from the post-entry bars: (close+0.2 - (close-0.2))
    # / close = 0.4/close per bar — small, not the pre-entry ~2x swing.
    assert float(pos["atr_pct"]) < 0.05
