"""Phase 3 — per-stream session-forced-exit RAIL WIRING (over the recalc loop).

Verifies the calendar-integrity rail is wired into ``recalc_active_positions``
(via ``run_session_forced_exit`` in ``_production_recalc_exit``):

- A (OKX, ``always_on``): the rail NEVER force-closes — crypto exit is
  byte-identical (parity), even at the FX-weekend / US-RTH close instants;
- B (Capital, ``fx_indices_cal``): force-flat before the weekend close (Friday
  22:00 UTC), NOT mid-week — and the close routes through
  ``close_specific_position``;
- C (Alpaca, ``us_equity_cal``): force-flat before the RTH close (no overnight),
  NOT mid-RTH — routed through ``close_specific_position``;
- TIME not PnL: a deep WINNER on B/C still flattens at the close (venue
  reality); a mid-session LOSER is NOT force-closed by THIS rail (the #26 FSM /
  G6 stop owns that, never a loss/drawdown reaction here).

CALENDAR INTEGRITY, not a P&L throttle: per-position close only — no size
change, no entry block, no strategy/bot halt. always_on (A) increments NO
counter → A identical.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import uuid

import pytest

from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import recalc_active_positions
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState


def _utc_ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    base = dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC)
    return int(base.timestamp())


# Calendar instants. 2026-05-29 = Friday; 2026-05-27 = Wednesday.
_FRI_WEEKEND_CLOSE_IMMINENT = _utc_ts(2026, 5, 29, 21, 55)  # 5 min before 22:00 UTC
_WED_MIDWEEK = _utc_ts(2026, 5, 27, 12, 0)
# July (EDT): RTH close 16:00 ET == 20:00 UTC.
_RTH_CLOSE_IMMINENT = _utc_ts(2026, 7, 15, 19, 55)  # 5 min before 20:00 UTC
_RTH_MIDDAY = _utc_ts(2026, 7, 15, 17, 0)  # 13:00 ET, mid-RTH

_VENUE_META = {
    "okx": ("BTC-USDT", "crypto:BTC"),
    "capital": ("EUR_USD", "cfd:FX_MAJORS"),
    "alpaca": ("AAPL", "equity:MEGA_CAP"),
}


class _MockGPTClient:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or ['{"decision":"HOLD","reason":"x"}']
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                outer.calls.append(kwargs)
                idx = min(len(outer.calls) - 1, len(outer.responses) - 1)

                class _Block:
                    text = outer.responses[idx]

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


def _seed(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str,
    side: str = "long",
    entry_price: float = 100.0,
    last_price: float = 100.1,
    opened_ts: int,
    strategy: str = "s",
) -> None:
    symbol, group = _VENUE_META[venue]
    instrument = f"{venue}:{symbol}"
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.001, 'open', ?, 0)",
        (
            position_id, venue, symbol, group, strategy, strategy, strategy,
            side, opened_ts,
        ),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, ?, ?, ?, ?, 0.001, ?, 80.0, 0.05, 1.0, 0.0, 0, ?, ?, "
        " 'filled')",
        (
            uuid.uuid4().hex, opened_ts * 1000, strategy, instrument, venue,
            side, entry_price, position_id, uuid.uuid4().hex,
        ),
    )
    for i in range(20):
        ts = opened_ts - (20 - i) * 60
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, 100.0, 10000.0, 1, ?, ?, "
            " ?, 1.0, 'rest')",
            (
                instrument, group, venue, symbol, ts, entry_price,
                last_price + 0.5, last_price - 0.5, last_price, last_price,
                last_price, last_price,
            ),
        )


def _trade(position_id: str, venue: str, side: str = "long") -> SimulatedTrade:
    symbol, group = _VENUE_META[venue]
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue=venue, symbol=symbol,
        strategy_id="s", side=side, entry_price=100.0, notional_usd=80.0,
        open_ts=0, position_id=position_id, correlation_group=group,
        underlying_group_id=group,
    )


def _regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


async def _recalc(
    conn: sqlite3.Connection, state: ProdLoopState, *, now_ts: int,
) -> int:
    return await recalc_active_positions(
        conn, state=state, now_ts=now_ts,
        gpt_client=_MockGPTClient(['{"decision":"HOLD"}'] * 5),
        phase="P1", lookup_regime=_regime,
        close_specific=close_specific_position,
    )


def _status(conn: sqlite3.Connection, position_id: str) -> str:
    r = conn.execute(
        "SELECT status FROM positions WHERE position_id = ?", (position_id,),
    ).fetchone()
    return str(r[0])


# ---------------------------------------------------------------------------
# A — always_on (OKX): the rail NEVER fires, even at the close instants.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_okx_never_force_closed_at_fx_weekend_close(
    memdb: sqlite3.Connection,
) -> None:
    """A (always_on) at the FX-weekend-close instant → NOT force-closed by the
    rail (crypto is 24/7, byte-identical). last 100.1 keeps the #26 FSM from
    closing too (winner, inside trail, fresh)."""
    opened = _FRI_WEEKEND_CLOSE_IMMINENT - 300
    _seed(memdb, position_id="a1", venue="okx", opened_ts=opened, last_price=100.1)
    state = ProdLoopState()
    state.open_trades = [_trade("a1", "okx")]
    await _recalc(memdb, state, now_ts=_FRI_WEEKEND_CLOSE_IMMINENT)
    assert _status(memdb, "a1") == "open"  # parity — rail never fired
    assert state.recalc_session_forced_exit == 0


@pytest.mark.asyncio
async def test_okx_never_force_closed_at_rth_close(
    memdb: sqlite3.Connection,
) -> None:
    opened = _RTH_CLOSE_IMMINENT - 300
    _seed(memdb, position_id="a2", venue="okx", opened_ts=opened, last_price=100.1)
    state = ProdLoopState()
    state.open_trades = [_trade("a2", "okx")]
    await _recalc(memdb, state, now_ts=_RTH_CLOSE_IMMINENT)
    assert _status(memdb, "a2") == "open"
    assert state.recalc_session_forced_exit == 0


# ---------------------------------------------------------------------------
# B — fx_indices_cal (Capital): force-flat before the weekend close, not before.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capital_force_flat_before_weekend_close(
    memdb: sqlite3.Connection,
) -> None:
    opened = _FRI_WEEKEND_CLOSE_IMMINENT - 300
    _seed(memdb, position_id="b1", venue="capital", opened_ts=opened, last_price=100.1)
    state = ProdLoopState()
    state.open_trades = [_trade("b1", "capital")]
    await _recalc(memdb, state, now_ts=_FRI_WEEKEND_CLOSE_IMMINENT)
    assert _status(memdb, "b1") == "closed"  # forced flat (routed via close_specific)
    assert state.recalc_session_forced_exit == 1
    # The #26 precise-exit did NOT fire — the calendar rail did.
    assert state.recalc_precise_exit == 0


@pytest.mark.asyncio
async def test_capital_not_force_flat_midweek(memdb: sqlite3.Connection) -> None:
    """Mid-week the FX market is open, no close imminent → the rail does NOT
    force-close; the #26 FSM / stop owns mid-session exits."""
    opened = _WED_MIDWEEK - 300
    _seed(memdb, position_id="b2", venue="capital", opened_ts=opened, last_price=100.1)
    state = ProdLoopState()
    state.open_trades = [_trade("b2", "capital")]
    await _recalc(memdb, state, now_ts=_WED_MIDWEEK)
    assert _status(memdb, "b2") == "open"
    assert state.recalc_session_forced_exit == 0


# ---------------------------------------------------------------------------
# C — us_equity_cal (Alpaca): force-flat before RTH close (no overnight), not
#     mid-RTH.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alpaca_force_flat_before_rth_close(
    memdb: sqlite3.Connection,
) -> None:
    opened = _RTH_CLOSE_IMMINENT - 300
    _seed(memdb, position_id="c1", venue="alpaca", opened_ts=opened, last_price=100.1)
    state = ProdLoopState()
    state.open_trades = [_trade("c1", "alpaca")]
    await _recalc(memdb, state, now_ts=_RTH_CLOSE_IMMINENT)
    assert _status(memdb, "c1") == "closed"  # no-overnight forced flat
    assert state.recalc_session_forced_exit == 1
    assert state.recalc_precise_exit == 0


@pytest.mark.asyncio
async def test_alpaca_not_force_flat_mid_rth(memdb: sqlite3.Connection) -> None:
    opened = _RTH_MIDDAY - 300
    _seed(memdb, position_id="c2", venue="alpaca", opened_ts=opened, last_price=100.1)
    state = ProdLoopState()
    state.open_trades = [_trade("c2", "alpaca")]
    await _recalc(memdb, state, now_ts=_RTH_MIDDAY)
    assert _status(memdb, "c2") == "open"
    assert state.recalc_session_forced_exit == 0


# ---------------------------------------------------------------------------
# TIME not PnL — deep winner flattens at close; mid-session loser does NOT.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deep_winner_capital_still_flattens_at_close(
    memdb: sqlite3.Connection,
) -> None:
    """A deep WINNER on B (last >> entry) still flattens at the weekend close —
    winners RUN until the calendar forces flat (venue reality, not a throttle)."""
    opened = _FRI_WEEKEND_CLOSE_IMMINENT - 300
    _seed(
        memdb, position_id="b-win", venue="capital", opened_ts=opened,
        entry_price=100.0, last_price=130.0,  # deep winner
    )
    state = ProdLoopState()
    state.open_trades = [_trade("b-win", "capital")]
    await _recalc(memdb, state, now_ts=_FRI_WEEKEND_CLOSE_IMMINENT)
    assert _status(memdb, "b-win") == "closed"
    assert state.recalc_session_forced_exit == 1


@pytest.mark.asyncio
async def test_mid_session_loser_capital_not_force_closed_by_rail(
    memdb: sqlite3.Connection,
) -> None:
    """A mid-session LOSER on B (last < entry) is NOT force-closed by THIS rail
    mid-week — the rail is TIME-only, never a loss/drawdown reaction. (The #26
    FSM / G6 -1.0R stop owns a losing mid-session position; the rail does not.)
    last 99.9 keeps it a shallow loser inside the #26 trail so only the rail
    could (but must not) close it here."""
    opened = _WED_MIDWEEK - 300
    _seed(
        memdb, position_id="b-lose", venue="capital", opened_ts=opened,
        entry_price=100.0, last_price=99.9,  # shallow mid-session loser
    )
    state = ProdLoopState()
    state.open_trades = [_trade("b-lose", "capital")]
    await _recalc(memdb, state, now_ts=_WED_MIDWEEK)
    assert _status(memdb, "b-lose") == "open"  # rail did NOT force-close
    assert state.recalc_session_forced_exit == 0
