"""FIX-4 — the live tick loop must NOT force-close a position every tick.

Root cause (live DB forensic): ``_production_tick._run_tick`` had an
unconditional tail-of-tick drain ::

    if state.open_trades:
        await close_oldest_with_real_pnl(...)

that closed ``state.open_trades[0]`` at the END of EVERY 5s tick with NO exit
predicate (no stop, no PnL, no holding-bars gate). A position opened during a
tick's pipeline fan-out was force-closed within the same/next tick, so the
multi-bar adaptive exit (G6/G7) never managed a live position — every position
closed at holding_bars=0.

These tests pin the fix:
1. Source-level regression guard — ``_run_tick`` no longer carries the
   unconditional ``close_oldest_with_real_pnl`` tail call.
2. Behavioral — when G6 returns HOLD across multiple ticks, the position
   STAYS OPEN (close fires only on a genuine exit decision). This is the
   precise-exit strengthening of flow_not_block (Jin's loss-defense): a
   position can now live multiple bars so G6/G7 can engage.
3. Behavioral — a genuine G6 EXIT_NOW still closes the specific position
   (the real exit path is untouched).

DEMO/PAPER only; virtual funds. No defensive throttle, no size dampen, no
chain multiplier — this only removes a false unconditional close.
"""

from __future__ import annotations

import inspect
import sqlite3
import uuid

import pytest

from polaris.scripts import _production_tick
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import recalc_active_positions
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


class _MockGPTClient:
    """Returns a queued response per call (mirrors test_live_recalc_loop)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                outer.calls.append(kwargs)
                idx = min(len(outer.calls) - 1, len(outer.responses) - 1)
                text = outer.responses[idx]

                class _Block:
                    pass

                _Block.text = text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


def _seed_position_and_fill(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str = "okx",
    symbol: str = "BTC-USDT",
    side: str = "long",
    entry_price: float = 80_000.0,
    last_price: float = 80_400.0,
    strategy: str = "vb",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, 0)",
        (
            position_id, venue, symbol, "crypto:BTC", strategy, strategy,
            strategy, side, 0.001, NOW,
        ),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, strategy, f"{venue}:{symbol}", venue,
            side, 0.001, entry_price, 80.0, 0.05, 1.0, 0.0,
            position_id, uuid.uuid4().hex,
        ),
    )
    instrument_id = f"{venue}:{symbol}"
    for i in range(20):
        ts = NOW - (20 - i) * 60
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rest')",
            (
                instrument_id, "crypto:BTC", venue, symbol, ts,
                entry_price, last_price + 50.0, entry_price - 50.0,
                last_price, 100.0, 100.0 * last_price, 1,
                last_price, last_price, last_price, 1.0,
            ),
        )


def _trade_for(position_id: str) -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex,
        venue="okx",
        symbol="BTC-USDT",
        strategy_id="vb",
        side="long",
        entry_price=80_000.0,
        notional_usd=80.0,
        open_ts=NOW,
        position_id=position_id,
        correlation_group="crypto:BTC",
        underlying_group_id="crypto:BTC",
    )


def _lookup_regime_stub(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


# ---------------------------------------------------------------------------
# 1. Source-level regression guard
# ---------------------------------------------------------------------------


def test_run_tick_has_no_unconditional_tail_close() -> None:
    """``_run_tick`` must not unconditionally close the oldest trade at tick tail.

    The forensic bug: ``if state.open_trades: await close_oldest_with_real_pnl``
    at the END of every tick closed a position with no exit predicate. The fix
    removes that drain so closes happen ONLY through the G6/G7 adaptive-exit
    path (close_specific_position on EXIT_NOW).
    """
    src = inspect.getsource(_production_tick._run_tick)
    assert "await close_oldest_with_real_pnl(" not in src, (
        "FIX-4: _run_tick must not invoke the unconditional tail-of-tick "
        "close_oldest_with_real_pnl drain — a position must survive multiple "
        "ticks so G6/G7 can manage the exit."
    )


# ---------------------------------------------------------------------------
# 2. HOLD across ticks → position stays open (multi-bar holding)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hold_keeps_position_open_across_ticks(
    memdb: sqlite3.Connection,
) -> None:
    """G6 HOLD on two consecutive ticks must NOT close the position.

    Proves a position can live multiple bars (holding_bars > 0) when no exit
    condition fires — the precise-exit prerequisite. The recalc sweep is the
    ONLY close trigger now, and it closes only on EXIT_NOW.
    """
    _seed_position_and_fill(memdb, position_id="pos-hold")
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-hold")]
    haiku = _MockGPTClient(responses=['{"decision":"HOLD","reason":"hold"}'] * 4)

    for delta in (0, 5):
        await recalc_active_positions(
            memdb, state=state, now_ts=NOW + delta, gpt_client=haiku,
            phase="P1", lookup_regime=_lookup_regime_stub,
            close_specific=close_specific_position,
        )

    # Position survives both ticks — no unconditional close.
    assert len(state.open_trades) == 1
    assert state.open_trades[0].position_id == "pos-hold"
    assert len(state.closed_trades) == 0
    assert state.recalc_exit_now == 0
    status = memdb.execute(
        "SELECT status FROM positions WHERE position_id = 'pos-hold'"
    ).fetchone()[0]
    assert status == "open"


# ---------------------------------------------------------------------------
# 3. Genuine EXIT_NOW still closes (real exit path intact)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_now_still_closes_position(memdb: sqlite3.Connection) -> None:
    """A genuine deterministic exit closes the specific position.

    ai_conductor P3 (2026-05-30): G6 GPT is removed. A hard loser
    (pnl_r << -1.0R) closes within the tick via the deterministic exit path
    (FSM precise-exit / G6 -1.0R rail backstop) — close_specific_position, never
    an unconditional/FIFO close. No GPT call is needed.
    """
    _seed_position_and_fill(memdb, position_id="pos-exit", last_price=74_000.0)
    state = ProdLoopState()
    state.open_trades = [_trade_for("pos-exit")]
    haiku = _MockGPTClient(responses=['{"decision":"HOLD","reason":"x"}'])

    await recalc_active_positions(
        memdb, state=state, now_ts=NOW, gpt_client=haiku, phase="P1",
        lookup_regime=_lookup_regime_stub, close_specific=close_specific_position,
    )

    assert len(state.open_trades) == 0
    status = memdb.execute(
        "SELECT status FROM positions WHERE position_id = 'pos-exit'"
    ).fetchone()[0]
    assert status == "closed"
    # No GPT call closed a deterministic loser.
    assert haiku.calls == []
