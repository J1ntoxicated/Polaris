"""#26 precise-exit WIRING — tick-loop integration over the recalc engine.

Verifies the precise-exit engine is wired into ``recalc_active_positions``:
- peak/MFE tracking persists to the positions row each tick;
- the ATR-trailing stop ratchets and persists (never loosens);
- the MFE FSM advances and persists;
- a round-tripped winner (protected BEP) closes;
- a stale loser closes but a winner does NOT;
- the dropped G7 EXIT_NOW decision now actually closes the position;
- the G6 EXIT_NOW path is still honoured (hard rail unchanged).

EXPECTANCY, not a throttle: per-position close only — no size change, no entry
block, no strategy/bot halt. The G6 -1.0R hard stop_hit rail is in the G6 gate
(deterministic), not this module; nothing here removes it.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.live_recalc.exit_engine import EXIT_LOSER_TIMEOUT_SEC
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import recalc_active_positions
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000


class _MockGPTClient:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or ['{"decision":"HOLD","reason":"x"}']
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):  # noqa: ANN001
                outer.calls.append(kwargs)
                idx = min(len(outer.calls) - 1, len(outer.responses) - 1)
                text = outer.responses[idx]

                class _Block:
                    text = ""

                _Block.text = text

                class _Resp:
                    content = [_Block()]
                    usage = None

                return _Resp()

        self.messages = _Messages()


def _seed(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    side: str = "long",
    entry_price: float = 100.0,
    last_price: float = 100.0,
    opened_ts: int = NOW,
    strategy: str = "vb",
    band: float = 0.5,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', ?, ?, ?, ?, 0.001, "
        " 'open', ?, 0)",
        (position_id, strategy, strategy, strategy, side, opened_ts),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, ?, 'okx:BTC-USDT', 'okx', ?, 0.001, ?, 80.0, 0.05, "
        " 1.0, 0.0, 0, ?, ?, 'filled')",
        (
            uuid.uuid4().hex, NOW * 1000, strategy, side, entry_price,
            position_id, uuid.uuid4().hex,
        ),
    )
    for i in range(20):
        ts = NOW - (20 - i) * 60
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
            " ?, ?, ?, ?, 100.0, 10000.0, 1, ?, ?, ?, 1.0, 'rest')",
            (
                ts, entry_price, last_price + band, last_price - band,
                last_price, last_price, last_price, last_price,
            ),
        )


def _set_last_price(
    conn: sqlite3.Connection, *, last_price: float, band: float = 0.5,
    base_ts: int = NOW,
) -> None:
    """Append a NEWER 1m bar so ``load_active_position_rows`` reads the new
    last_price WITHOUT touching the positions row (preserves tracked state)."""
    conn.execute(
        "INSERT OR REPLACE INTO bars "
        "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
        " ts, open, high, low, close, volume, notional_usd, trade_count, "
        " vwap, bid_close, ask_close, spread_bps_close, source) "
        "VALUES ('okx:BTC-USDT', 'crypto:BTC', 'okx', 'BTC-USDT', '1m', ?, "
        " ?, ?, ?, ?, 100.0, 10000.0, 1, ?, ?, ?, 1.0, 'rest')",
        (
            base_ts + 60, last_price, last_price + band, last_price - band,
            last_price, last_price, last_price, last_price,
        ),
    )


def _trade(position_id: str, side: str = "long") -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="BTC-USDT",
        strategy_id="vb", side=side, entry_price=100.0, notional_usd=80.0,
        open_ts=NOW, position_id=position_id, correlation_group="crypto:BTC",
        underlying_group_id="crypto:BTC",
    )


def _regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


async def _recalc(
    conn: sqlite3.Connection, state: ProdLoopState, *, now_ts: int = NOW,
    gpt: _MockGPTClient | None = None,
) -> int:
    return await recalc_active_positions(
        conn, state=state, now_ts=now_ts,
        gpt_client=gpt or _MockGPTClient(['{"decision":"HOLD"}'] * 5),
        phase="P1", lookup_regime=_regime,
        close_specific=close_specific_position,
    )


def _pos_row(conn: sqlite3.Connection, position_id: str) -> dict:
    r = conn.execute(
        "SELECT status, stop_price, peak_price, trough_price, mfe_r, mae_r, "
        "exit_state FROM positions WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return {
        "status": r[0], "stop_price": r[1], "peak_price": r[2],
        "trough_price": r[3], "mfe_r": r[4], "mae_r": r[5], "exit_state": r[6],
    }


# --- peak / MFE tracking persists ------------------------------------------


@pytest.mark.asyncio
async def test_precise_exit_tracks_peak_and_mfe(memdb: sqlite3.Connection) -> None:
    # Winner well above entry but inside the trail (atr ~0.5%).
    _seed(memdb, position_id="pos-win", entry_price=100.0, last_price=100.4)
    state = ProdLoopState()
    state.open_trades = [_trade("pos-win")]
    await _recalc(memdb, state)
    row = _pos_row(memdb, "pos-win")
    assert row["status"] == "open"  # not closed (winner, fresh)
    assert row["peak_price"] is not None
    assert row["peak_price"] >= 100.4
    assert row["mfe_r"] is not None and row["mfe_r"] > 0.0
    assert row["stop_price"] is not None  # ATR trail persisted


# --- ATR stop ratchets up not down across two ticks ------------------------


@pytest.mark.asyncio
async def test_atr_stop_ratchets_across_ticks(memdb: sqlite3.Connection) -> None:
    _seed(memdb, position_id="pos-r", entry_price=100.0, last_price=100.3)
    state = ProdLoopState()
    state.open_trades = [_trade("pos-r")]
    await _recalc(memdb, state)
    stop1 = _pos_row(memdb, "pos-r")["stop_price"]
    # New higher bar → peak advances → stop ratchets UP (positions row intact).
    _set_last_price(memdb, last_price=101.0)
    await _recalc(memdb, state, now_ts=NOW + 5)
    stop2 = _pos_row(memdb, "pos-r")["stop_price"]
    assert stop2 >= stop1  # ratcheted, never loosened


# --- FSM advances on MFE ----------------------------------------------------


@pytest.mark.asyncio
async def test_fsm_advances_to_protected(memdb: sqlite3.Connection) -> None:
    # band 0.05 → atr_one 0.1, atr_r 0.2; last 100.3 → mfe 1.5R → PROTECTED.
    _seed(
        memdb, position_id="pos-fsm", entry_price=100.0, last_price=100.3,
        band=0.05,
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-fsm")]
    await _recalc(memdb, state)
    row = _pos_row(memdb, "pos-fsm")
    assert row["status"] == "open"
    assert row["exit_state"] in ("protected", "harvest")


# --- protected BEP closes a round-tripped winner ---------------------------


@pytest.mark.asyncio
async def test_protected_bep_closes_round_tripped_winner(
    memdb: sqlite3.Connection,
) -> None:
    _seed(
        memdb, position_id="pos-rt", entry_price=100.0, last_price=100.3,
        band=0.05,
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-rt")]
    await _recalc(memdb, state)  # advance to PROTECTED
    assert _pos_row(memdb, "pos-rt")["status"] == "open"
    assert _pos_row(memdb, "pos-rt")["exit_state"] in ("protected", "harvest")
    # Round-trip: price falls back below entry → protected BEP close (positions
    # row preserved so the PROTECTED state survives to this tick).
    _set_last_price(memdb, last_price=99.7, band=0.05)
    await _recalc(memdb, state, now_ts=NOW + 5)
    assert _pos_row(memdb, "pos-rt")["status"] == "closed"
    assert state.recalc_precise_exit == 1


# --- loser timeout closes a stale loser but NOT a winner -------------------


@pytest.mark.asyncio
async def test_loser_timeout_closes_stale_loser(memdb: sqlite3.Connection) -> None:
    opened = NOW - int(EXIT_LOSER_TIMEOUT_SEC) - 60
    _seed(
        memdb, position_id="pos-stale", entry_price=100.0, last_price=99.8,
        opened_ts=opened,
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-stale")]
    await _recalc(memdb, state)
    assert _pos_row(memdb, "pos-stale")["status"] == "closed"
    assert state.recalc_precise_exit == 1


@pytest.mark.asyncio
async def test_loser_timeout_does_not_close_aged_winner(
    memdb: sqlite3.Connection,
) -> None:
    opened = NOW - int(EXIT_LOSER_TIMEOUT_SEC) - 60
    _seed(
        memdb, position_id="pos-aged-win", entry_price=100.0, last_price=100.3,
        opened_ts=opened,
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-aged-win")]
    await _recalc(memdb, state)
    assert _pos_row(memdb, "pos-aged-win")["status"] == "open"
    assert state.recalc_precise_exit == 0


# --- Component B: exit horizon ∝ timeframe ----------------------------------


def test_loser_timeout_for_strategy_scales_to_timeframe() -> None:
    from polaris.scripts._production_recalc_exit import (
        EXIT_LOSER_TIMEOUT_MIN_BARS,
        _loser_timeout_for_strategy,
    )

    # tsmom = 1H → at least MIN_BARS × 3600 (>= 7200s with default 2 bars).
    tsmom_timeout = _loser_timeout_for_strategy("tsmom")
    assert tsmom_timeout >= EXIT_LOSER_TIMEOUT_MIN_BARS * 3600
    assert tsmom_timeout > EXIT_LOSER_TIMEOUT_SEC  # NOT the flat 900s
    # volume_burst = 1m → max(900, 2×60=120) = 900s (fast strategy stays short).
    assert _loser_timeout_for_strategy("volume_burst") == EXIT_LOSER_TIMEOUT_SEC
    # Unregistered id → flat default (back-compat).
    assert _loser_timeout_for_strategy("vb") == EXIT_LOSER_TIMEOUT_SEC


@pytest.mark.asyncio
async def test_1h_thesis_not_force_closed_at_900s(
    memdb: sqlite3.Connection,
) -> None:
    # A tsmom (1H) loser aged 901s — past the flat 900s but WITHIN its 7200s
    # horizon — must NOT be force-closed (hold-to-thesis). last_price 99.8 keeps
    # it a small loser, never touched profit, so only the timeout path applies.
    opened = NOW - int(EXIT_LOSER_TIMEOUT_SEC) - 1  # 901s held
    _seed(
        memdb, position_id="pos-1h", entry_price=100.0, last_price=99.8,
        opened_ts=opened, strategy="tsmom",
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-1h")]
    await _recalc(memdb, state)
    assert _pos_row(memdb, "pos-1h")["status"] == "open"  # horizon respected
    assert state.recalc_precise_exit == 0


@pytest.mark.asyncio
async def test_1m_strategy_still_times_out_at_900s(
    memdb: sqlite3.Connection,
) -> None:
    # A 1m-equivalent loser (unregistered "vb" → flat 900s) past 900s STILL
    # times out — the short horizon for fast strategies is preserved.
    opened = NOW - int(EXIT_LOSER_TIMEOUT_SEC) - 60
    _seed(
        memdb, position_id="pos-1m", entry_price=100.0, last_price=99.8,
        opened_ts=opened, strategy="vb",
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-1m")]
    await _recalc(memdb, state)
    assert _pos_row(memdb, "pos-1m")["status"] == "closed"
    assert state.recalc_precise_exit == 1


# --- G7 EXIT_NOW dropped-decision bug fix: now actually closes -------------


@pytest.mark.asyncio
async def test_g7_exit_now_now_closes_position(memdb: sqlite3.Connection) -> None:
    # Strong winner (pnl_r > 0.7R) → deterministic G6 ADJUST_EXIT chains G7;
    # G7 GPT emits EXIT_NOW → position must close now. tight band keeps the
    # precise-exit engine from closing first (winner, harvest, not stopped).
    # ai_conductor P3: G6 is deterministic, so the mock only feeds G7.
    _seed(memdb, position_id="pos-g7x", entry_price=100.0, last_price=101.0, band=0.1)
    state = ProdLoopState()
    state.open_trades = [_trade("pos-g7x")]
    gpt = _MockGPTClient([
        '{"decision":"EXIT_NOW","reason":"regime flip","new_exit_atr":0.0}',  # G7
    ])
    await _recalc(memdb, state, gpt=gpt)
    assert _pos_row(memdb, "pos-g7x")["status"] == "closed"
    assert state.recalc_g6_calls == 1
    assert state.recalc_g7_calls == 1
    assert state.recalc_exit_now == 1
    # precise-exit did NOT close it (the G7 EXIT_NOW did).
    assert state.recalc_precise_exit == 0
    # G7 is the only GPT call — G6 is deterministic (no per-position GPT).
    assert len(gpt.calls) == 1


# --- G6 hard loss rail unchanged (deterministic EXIT_NOW at pnl_r <= -1R) ---


@pytest.mark.asyncio
async def test_g6_hard_rail_exit_now_unchanged(memdb: sqlite3.Connection) -> None:
    """The deterministic G6 -1.0R EXIT_NOW rail is unchanged (ai_conductor P3).

    A hard loser closes within one tick (the FSM precise-exit owns the stop
    close; the G6 rail is the deterministic backstop behind it). No GPT call is
    made for G6.
    """
    _seed(memdb, position_id="pos-g6x", entry_price=100.0, last_price=98.0, band=0.1)
    state = ProdLoopState()
    state.open_trades = [_trade("pos-g6x")]
    gpt = _MockGPTClient(['{"decision":"HOLD","reason":"x"}'])
    await _recalc(memdb, state, gpt=gpt)
    # The hard loser is gone within the tick (FSM stop / G6 rail backstop).
    assert _pos_row(memdb, "pos-g6x")["status"] == "closed"
    # No GPT call was needed to close a deterministic loser.
    assert gpt.calls == []
