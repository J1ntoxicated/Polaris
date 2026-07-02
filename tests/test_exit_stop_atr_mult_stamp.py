"""[P2-14] positions.stop_atr_mult observability stamp.

DEMO/PAPER virtual funds. AGGRESSIVE / flow_not_block — MEASUREMENT ONLY, never
gates sizing / entry / exit-timing. Companion to the [P1-8] BEP-arm/trail
invariant fix ([[trade_mess_full_audit_2026-07-02]]): stamps the RESOLVED
``_stop_atr_mult_for_strategy`` result on the ``positions`` row so a
floor-bound / wide-ruler position is directly readable off the row instead of
re-derived. ``None`` (a legacy row / a caller that never resolves the mult)
leaves the column untouched — byte-identical.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.metrics.risk_unit import STOP_ATR_MULT
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import ActivePositionRow
from polaris.scripts._production_recalc_exit import (
    persist_exit_state,
    run_precise_exit,
)
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.exit_strategy_config import (
    WEEKEND_MAKER_STOP_ATR_MULT,
    _stop_atr_mult_for_strategy,
)
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_800_000_000
WEEKEND_STRATEGY_ID = "weekend_thin_book_flush_maker"


def _seed_open(
    conn: sqlite3.Connection, *, position_id: str, strategy_id: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, stop_price, peak_price, trough_price, "
        " exit_state) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', ?, ?, ?, "
        " 'long', 0.001, 'open', ?, 0, 90.0, 100.0, 99.9, 'open')",
        (position_id, strategy_id, strategy_id, strategy_id, NOW),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, ?, 'okx:BTC-USDT', 'okx', 'buy', 0.001, 100.0, "
        " 80.0, 0.05, 1.0, 0.0, 0, ?, ?, 'filled')",
        (uuid.uuid4().hex, NOW * 1000, strategy_id, position_id, uuid.uuid4().hex),
    )


def _trade(position_id: str, strategy_id: str) -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="BTC-USDT",
        strategy_id=strategy_id, side="long", entry_price=100.0,
        notional_usd=80.0, open_ts=NOW, position_id=position_id,
        correlation_group="crypto:BTC", underlying_group_id="crypto:BTC",
    )


def _regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


def _stamped_mult(conn: sqlite3.Connection, position_id: str) -> float | None:
    r = conn.execute(
        "SELECT stop_atr_mult FROM positions WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return None if r is None else r[0]


async def _run(
    conn: sqlite3.Connection, *, position_id: str, strategy_id: str,
) -> None:
    state = ProdLoopState()
    state.open_trades = [_trade(position_id, strategy_id)]
    pos = ActivePositionRow()
    pos.update({
        "position_id": position_id, "venue": "okx", "symbol": "BTC-USDT",
        "side": "long", "active_strategy_id": strategy_id,
        "strategy": strategy_id, "stop_price": 90.0, "peak_price": 100.0,
        "trough_price": 99.9, "exit_state": "open",
    })
    await run_precise_exit(
        conn=conn, state=state, pos=pos, side="long",
        entry_price=100.0, last_price=100.5, atr_pct=0.019, pnl_r=0.1,
        held_seconds=60, now_ts=NOW, close_specific=close_specific_position,
        lookup_regime=_regime, gpt_client=None, phase="P0",
        real_roundtrip=False, okx_adapter=None, capital_session=None,
    )


@pytest.mark.asyncio
async def test_default_strategy_stamps_ssot_mult(memdb: sqlite3.Connection) -> None:
    _seed_open(memdb, position_id="pos-ssot", strategy_id="session_breakout")
    await _run(memdb, position_id="pos-ssot", strategy_id="session_breakout")
    assert _stamped_mult(memdb, "pos-ssot") == STOP_ATR_MULT == 2.0


@pytest.mark.asyncio
async def test_weekend_maker_floor_bound_strategy_stamps_widened_mult(
    memdb: sqlite3.Connection,
) -> None:
    # The floor-bound / wide-ruler binding path — readable directly off the row.
    _seed_open(memdb, position_id="pos-weekend", strategy_id=WEEKEND_STRATEGY_ID)
    await _run(memdb, position_id="pos-weekend", strategy_id=WEEKEND_STRATEGY_ID)
    stamped = _stamped_mult(memdb, "pos-weekend")
    assert stamped == WEEKEND_MAKER_STOP_ATR_MULT == 3.0
    assert stamped != STOP_ATR_MULT  # distinguishable from the SSOT binding
    assert stamped == _stop_atr_mult_for_strategy(WEEKEND_STRATEGY_ID)


def test_persist_exit_state_none_leaves_column_untouched(
    memdb: sqlite3.Connection,
) -> None:
    # A caller that never resolves stop_atr_mult (stop_atr_mult=None, the
    # default) leaves the column untouched — byte-identical to pre-stamp.
    from polaris.core.live_recalc.exit_engine import init_exit_state

    _seed_open(memdb, position_id="pos-notouch", strategy_id="session_breakout")
    st = init_exit_state(entry_price=100.0, side="long")
    persist_exit_state(memdb, position_id="pos-notouch", st=st)
    assert _stamped_mult(memdb, "pos-notouch") is None
