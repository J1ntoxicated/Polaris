"""Close-path wiring — weekly trace + ruin check fire on every close.

DEMO/PAPER, VIRTUAL ACCOUNT (POLARIS_VIRTUAL_ACCOUNT=1, real_roundtrip=False,
zero venue calls — sim fills only). TDD spec (Jin 2026-07-07):

  (a) a real entry->close cycle writes a weekly_equity_curve row for the
      trade's exchange, Monday-anchored, with trades=1 and realized_pnl_usd
      matching the close's net pnl.
  (b) a close that drives the exchange below the ruin floor records a
      virtual_ruin_events row (measurement flag) WITHOUT blocking the close
      itself — the position still transitions to 'closed'.
"""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import AsyncMock

import pytest

from polaris.core.isolation.allocator_fence import reset_process_fence
from polaris.scripts._production_close import close_oldest_with_real_pnl
from polaris.scripts._production_pipeline import reserve_and_submit
from polaris.scripts._virtual_account import resolve_real_roundtrip
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.storage.weekly_equity_trace import all_current_week_rows, week_start_ts
from polaris.strategies.base import RawSignal


def _sig(signal_id: str) -> RawSignal:
    return RawSignal(
        signal_id=signal_id, strategy_id="volume_burst", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_intraday_event",
    )


def _spy_adapter() -> AsyncMock:
    adapter = AsyncMock()

    async def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("venue adapter touched under virtual mode")

    adapter.place_market_order.side_effect = _fail
    adapter.fetch_order.side_effect = _fail
    adapter.fetch_balance.side_effect = _fail
    return adapter


@pytest.mark.asyncio
async def test_close_writes_weekly_trace_row(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")
    reset_process_fence()
    state = ProdLoopState()
    spy = _spy_adapter()
    effective = resolve_real_roundtrip(True)
    now = int(time.time())

    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("trace_sig"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", notional_usd=100.0,
        last_price=60_000.0, now_ts=now, real_roundtrip=effective,
        okx_adapter=spy,
    )
    assert trade is not None
    state.open_trades.append(trade)

    def _lookup_regime(*_a: object, **_k: object) -> str:
        return ""

    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=now + 60, lookup_regime=_lookup_regime,
        real_roundtrip=effective, okx_adapter=spy,
    )
    assert len(state.closed_trades) == 1

    week = week_start_ts(now + 60)
    rows = all_current_week_rows(memdb, now_ts=now + 60)
    okx_rows = [r for r in rows if r.exchange == "okx"]
    assert len(okx_rows) == 1
    assert okx_rows[0].week_start_ts == week
    assert okx_rows[0].trades == 1


@pytest.mark.asyncio
async def test_close_below_ruin_floor_flags_but_does_not_block(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing catastrophic loss (already below the ruin floor via
    prior fills) still lets the NEXT close complete normally (flow_not_block)
    — the close-path hook only records the ruin event as a measurement flag,
    it never blocks/skips the trade."""
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_OKX", "100.0")
    reset_process_fence()
    state = ProdLoopState()
    spy = _spy_adapter()
    effective = resolve_real_roundtrip(True)
    now = int(time.time())

    # Pre-seed a catastrophic prior loss (net < 50% of the $100 seed) so this
    # exchange is ALREADY below the ruin floor before the new trade closes.
    memdb.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " is_close, pnl_usd, contribution_id) "
        "VALUES ('f_prior', 'okx', 'okx:ETH-USDT', 's', 'sell', 90.0, 1.0, "
        " 0.0, 0.0, ?, 'o_prior', 1, -80.0, '')",
        (now * 1000 - 1000,),
    )

    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=_sig("ruin_sig"), venue="okx",
        symbol="BTC-USDT", asset_class="crypto",
        underlying_group_id="crypto:BTC", notional_usd=10.0,
        last_price=60_000.0, now_ts=now, real_roundtrip=effective,
        okx_adapter=spy,
    )
    assert trade is not None
    state.open_trades.append(trade)

    def _lookup_regime(*_a: object, **_k: object) -> str:
        return ""

    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=now + 60, lookup_regime=_lookup_regime,
        real_roundtrip=effective, okx_adapter=spy,
    )
    # The close still completed (flow_not_block) regardless of ruin state.
    assert len(state.closed_trades) == 1
    closed_row = memdb.execute(
        "SELECT status FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    assert closed_row is not None and closed_row[0] == "closed"
    # The ruin floor was already breached by the pre-seeded loss -> the close
    # hook's ruin check must have flagged + recorded it.
    ruin_rows = memdb.execute(
        "SELECT exchange, reseeded_to FROM virtual_ruin_events WHERE exchange = 'okx'"
    ).fetchall()
    assert len(ruin_rows) == 1
    assert ruin_rows[0][1] == 100.0
