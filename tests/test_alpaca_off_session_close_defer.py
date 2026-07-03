"""P0 fix — off-session Alpaca close-retry defer (DEMO/PAPER, aggressive bias).

Forensic: ``run_precise_exit``'s #26 FSM decides close/hold on price/ATR alone
with NO session awareness, every ~5s recalc tick. For a ``us_equity_cal``
(Alpaca) position held off-RTH (overnight / weekend / a stale-overnight gap) a
decided close called the venue EVERY tick; Alpaca rejects (market closed)
every time, and the zombie-drain tally deliberately never counts an
off-session reject (a live position waiting for the reopen must not be
abandoned as a phantom zombie) — so the retry ran UNBOUNDED forever
off-session (observed 825 retries/symbol).

Fix: ``_close_trade_with_real_pnl`` defers the ENTIRE venue call for an
off-session Alpaca position — no reject counted, position preserved, retried
once at the next in-session tick. Capital/OKX are untouched (this predicate
only fires for ``us_equity_cal``).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import uuid
from typing import Any

import pytest

import polaris.scripts._production_close as pc
from polaris.core.data.fill_normalizer import Fill
from polaris.scripts._production_close import _close_trade_with_real_pnl
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

# 2026-07-15 12:00 UTC == 08:00 ET — before the 09:30 RTH open (off-session).
_OFF_SESSION_TS = int(
    dt.datetime(2026, 7, 15, 12, 0, tzinfo=dt.UTC).timestamp()
)
# 2026-07-15 19:55 UTC == 15:55 ET — inside RTH.
_IN_SESSION_TS = int(
    dt.datetime(2026, 7, 15, 19, 55, tzinfo=dt.UTC).timestamp()
)


def _seed_alpaca_position(
    conn: sqlite3.Connection, *, position_id: str, base_qty: float, opened_ts: int,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, 'alpaca', 'SELX', 'equity:SELX', 'equity_52wk_high_breakout', "
        " 'equity_52wk_high_breakout', 'equity_52wk_high_breakout', 'long', ?, "
        " 'open', ?, 0)",
        (position_id, base_qty, opened_ts),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'equity_52wk_high_breakout', 'alpaca:SELX', 'alpaca', "
        " 'long', ?, 10.0, ?, 0.01, 1.0, 0.0, 0, ?, ?, 'filled')",
        (uuid.uuid4().hex, opened_ts * 1000, base_qty, base_qty * 10.0,
         position_id, uuid.uuid4().hex),
    )


def _alpaca_trade(position_id: str, base_qty: float, *, open_ts: int) -> SimulatedTrade:
    t = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="alpaca", symbol="SELX",
        strategy_id="equity_52wk_high_breakout", side="long", entry_price=10.0,
        notional_usd=base_qty * 10.0, open_ts=open_ts, position_id=position_id,
    )
    t.base_qty = base_qty
    return t


def _regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


@pytest.mark.asyncio
async def test_off_session_close_defers_without_calling_venue(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_alpaca_position(
        memdb, position_id="pos-off", base_qty=2.0, opened_ts=_OFF_SESSION_TS - 3600,
    )
    state = ProdLoopState()
    trade = _alpaca_trade("pos-off", 2.0, open_ts=_OFF_SESSION_TS - 3600)
    state.open_trades = [trade]

    calls: list[str] = []

    async def _spy(**kwargs: Any) -> Fill:
        calls.append("called")
        raise AssertionError("venue close must NOT be called off-session")

    monkeypatch.setattr(pc, "_real_close_fill", _spy)
    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=_OFF_SESSION_TS,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True,
    )
    assert ok is False
    assert calls == []  # venue call never happened
    assert state.open_trades == [trade]  # position preserved
    assert trade.closed is False
    # No reject counted anywhere (the zombie-drain tally stays empty).
    assert state.close_reject_counts == {}
    assert state.venue_close_rejects == 0


@pytest.mark.asyncio
async def test_in_session_close_still_calls_venue(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: the defer must NOT suppress an in-session close — the existing
    behaviour (venue call + normal fill/reject handling) is unchanged during
    RTH."""
    _seed_alpaca_position(
        memdb, position_id="pos-on", base_qty=2.0, opened_ts=_IN_SESSION_TS - 3600,
    )
    state = ProdLoopState()
    trade = _alpaca_trade("pos-on", 2.0, open_ts=_IN_SESSION_TS - 3600)
    state.open_trades = [trade]

    async def _filled(**kwargs: Any) -> Fill:
        return Fill(
            venue="alpaca", instrument_id="alpaca:SELX",
            strategy_id="equity_52wk_high_breakout", side="sell", size_usd=22.0,
            fill_price=11.0, fee_usd=0.01, slippage_bps=0.0,
            ts_ms=_IN_SESSION_TS * 1000, order_id=uuid.uuid4().hex,
            base_qty=2.0, quote_qty=22.0,
        )

    monkeypatch.setattr(pc, "_real_close_fill", _filled)
    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=_IN_SESSION_TS,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True,
    )
    assert ok is True
    assert trade.closed is True


@pytest.mark.asyncio
async def test_off_session_defer_does_not_affect_okx(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defer predicate is Alpaca-only — an OKX position at the SAME
    off-session instant is unaffected (its venue call still fires)."""
    memdb.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES ('pos-okx', 'okx', 'SOL-USDT', 'crypto:SOL', 'tsmom', 'tsmom', "
        " 'tsmom', 'long', 2.0, 'open', ?, 0)",
        (_OFF_SESSION_TS - 3600,),
    )
    memdb.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, 'tsmom', 'okx:SOL-USDT', 'okx', 'long', 2.0, 100.0, "
        " 200.0, 0.05, 1.0, 0.0, 0, 'pos-okx', ?, 'filled')",
        (uuid.uuid4().hex, (_OFF_SESSION_TS - 3600) * 1000, uuid.uuid4().hex),
    )
    state = ProdLoopState()
    trade = SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="SOL-USDT",
        strategy_id="tsmom", side="long", entry_price=100.0, notional_usd=200.0,
        open_ts=_OFF_SESSION_TS - 3600, position_id="pos-okx",
    )
    trade.base_qty = 2.0
    state.open_trades = [trade]

    called: list[str] = []

    async def _filled(**kwargs: Any) -> Fill:
        called.append("called")
        return Fill(
            venue="okx", instrument_id="okx:SOL-USDT", strategy_id="tsmom",
            side="sell", size_usd=220.0, fill_price=110.0, fee_usd=0.02,
            slippage_bps=0.0, ts_ms=_OFF_SESSION_TS * 1000,
            order_id=uuid.uuid4().hex, base_qty=2.0, quote_qty=220.0,
        )

    monkeypatch.setattr(pc, "_real_close_fill", _filled)
    ok = await _close_trade_with_real_pnl(
        memdb, state=state, trade=trade, trade_idx=0, now_ts=_OFF_SESSION_TS,
        lookup_regime=_regime_stub, gpt_client=None, phase="P0",
        real_roundtrip=True,
    )
    assert ok is True
    assert called == ["called"]  # OKX venue call fires regardless of Alpaca RTH
