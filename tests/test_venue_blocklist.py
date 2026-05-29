"""Runtime non-tradeable compliance blocklist (Task 3 / D2).

DEMO/PAPER only. A 51155 (US-region compliance) reject permanently marks a
(venue, symbol) non-tradeable so focus + the order guard skip it — no
reservation, no fault. UPSERT bumps last_ts + hit_count on re-hit.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from polaris.core.isolation.blocklist import (
    add_blocklist,
    is_blocklisted,
    load_blocklist,
)


def test_add_then_is_blocklisted(memdb: sqlite3.Connection) -> None:
    assert is_blocklisted(memdb, "okx", "GAS-USDT") is False
    add_blocklist(
        memdb, "okx", "GAS-USDT", reason="compliance", code="51155",
        now_ts=1000,
    )
    assert is_blocklisted(memdb, "okx", "GAS-USDT") is True
    # Other symbols stay tradeable.
    assert is_blocklisted(memdb, "okx", "BTC-USDT") is False


def test_upsert_bumps_hit_count(memdb: sqlite3.Connection) -> None:
    add_blocklist(
        memdb, "okx", "TRUMP-USDT", reason="compliance", code="51155",
        now_ts=1000,
    )
    add_blocklist(
        memdb, "okx", "TRUMP-USDT", reason="compliance", code="51155",
        now_ts=2000,
    )
    row = memdb.execute(
        "SELECT hit_count, first_ts, last_ts FROM venue_blocklist "
        "WHERE venue='okx' AND symbol='TRUMP-USDT'"
    ).fetchone()
    assert row is not None
    assert int(row[0]) == 2
    assert int(row[1]) == 1000  # first_ts unchanged
    assert int(row[2]) == 2000  # last_ts bumped


def test_load_blocklist_returns_set(memdb: sqlite3.Connection) -> None:
    add_blocklist(memdb, "okx", "GAS-USDT", reason="compliance", code="51155", now_ts=1)
    add_blocklist(memdb, "okx", "TRUMP-USDT", reason="compliance", code="51155", now_ts=1)
    loaded = load_blocklist(memdb)
    assert loaded == {("okx", "GAS-USDT"), ("okx", "TRUMP-USDT")}


# ---------------------------------------------------------------------------
# Wiring: reserve_and_submit skips blocklisted before reserving
# ---------------------------------------------------------------------------


def _sig(signal_id: str, symbol: str) -> Any:
    from polaris.strategies.base import RawSignal

    return RawSignal(
        signal_id=signal_id, strategy_id="tsmom", symbol=symbol,
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )


@pytest.mark.asyncio
async def test_reserve_and_submit_skips_blocklisted(
    memdb: sqlite3.Connection,
) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import reserve_and_submit
    from polaris.scripts.production_paper_loop import ProdLoopState

    reset_process_fence()
    add_blocklist(
        memdb, "okx", "GAS-USDT", reason="compliance", code="51155", now_ts=1,
    )
    state = ProdLoopState()
    adapter = AsyncMock()
    adapter.place_market_order = AsyncMock()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("blk", "GAS-USDT"), venue="okx",
        symbol="GAS-USDT", asset_class="crypto",
        underlying_group_id="crypto:GAS", notional_usd=100.0,
        last_price=10.0, now_ts=int(time.time()),
        real_roundtrip=True, okx_adapter=adapter,
    )
    assert trade is None
    # No reservation made, adapter never driven, no fault.
    adapter.place_market_order.assert_not_awaited()
    res = memdb.execute(
        "SELECT COUNT(*) FROM allocator_reservations WHERE strategy_id='tsmom'"
    ).fetchone()[0]
    assert int(res) == 0
    assert state.fault_events == 0


def test_refresh_focus_excludes_blocklisted(memdb: sqlite3.Connection) -> None:
    from polaris.scripts._production_layers import refresh_focus_watchlist

    now = int(time.time())
    # Seed two active OKX symbols; blocklist one.
    for sym, grp in (("GAS-USDT", "crypto:GAS"), ("BTC-USDT", "crypto:BTC")):
        memdb.execute(
            "INSERT OR REPLACE INTO universe "
            "(venue, symbol, instrument_id, underlying_group_id, asset_class, "
            " quote_ccy, state, vol_24h_usd, spread_bps, atr_24h_pct, "
            " depth_10bps_usd, signal_density_7d, listing_ts, last_seen_ts, "
            " is_active, active_reason) "
            "VALUES ('okx', ?, ?, ?, 'crypto', 'USDT', 'live', 1e9, 2.0, 3.0, "
            "        1e6, 0.0, NULL, ?, 1, NULL)",
            (sym, f"okx:{sym}", grp, now),
        )
    add_blocklist(
        memdb, "okx", "GAS-USDT", reason="compliance", code="51155", now_ts=now,
    )
    refresh_focus_watchlist(memdb, cycle_ts=now)
    focus_syms = {
        str(r[0])
        for r in memdb.execute(
            "SELECT symbol FROM watchlist_focus "
            "WHERE cycle_ts = (SELECT MAX(cycle_ts) FROM watchlist_focus)"
        ).fetchall()
    }
    assert "GAS-USDT" not in focus_syms
    assert "BTC-USDT" in focus_syms
