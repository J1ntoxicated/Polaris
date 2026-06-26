"""Timeframe-aligned exit wiring — recalc / open-stamp / close-path integration.

DEMO/PAPER. Proves the two-part bug fix end to end:
#1 timeframe-blind ruler: a 1H ema_crossover position is now trailed on the 1H ATR
   (vehicle was spot_donchian; un-registered 2026-06-27 #56 stop-bleeders KILL —
   ema_crossover is the live OKX 1H TREND-bucket replacement, same coverage);
   (reproduce the 11-minute noise cut on the 1m ruler → resolved on 1H);
#2 anchorless denominator: mfe_r/mae_r/pnl_r are denominated by the ENTRY-time
   anchor stamped at open (volatility contraction can no longer inflate them).
Plus the regression rails: tick-engine scalp/momentum positions (unregistered
strategy ids) keep the 1m ruler byte-identical, legacy NULL-anchor rows fall
back gracefully, and the close path shares the anchored denominator.
Exit precision only — sizing chain untouched, no entry block, no halt.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

from polaris.core.metrics.risk_unit import r_budget_for_venue
from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_close_helpers import (
    _close_excursion_r,
    real_pnl_r_from_fills,
)
from polaris.scripts._production_recalc import (
    load_active_position_rows,
    recalc_active_positions,
)
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000
IID = "okx:BTC-USDT"


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


def _seed_bars(
    conn: sqlite3.Connection, *, interval: str, n: int, band: float,
    close: float = 100.0, end_ts: int = NOW,
) -> None:
    sec = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600, "1D": 86400}[interval]
    for i in range(n):
        ts = end_ts - (n - 1 - i) * sec
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, 'crypto:BTC', 'okx', 'BTC-USDT', ?, ?, ?, ?, ?, ?, "
            " 100.0, 1e4, 1, ?, ?, ?, 1.0, 'rest')",
            (IID, interval, ts, close, close + band, close - band, close,
             close, close, close),
        )


def _seed_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    strategy: str,
    side: str = "long",
    entry_price: float = 100.0,
    opened_ts: int = NOW,
    entry_atr_pct: float | None = None,
    entry_atr_timeframe: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count, entry_atr_pct, entry_atr_timeframe) "
        "VALUES (?, 'okx', 'BTC-USDT', 'crypto:BTC', ?, ?, ?, ?, 0.001, "
        " 'open', ?, 0, ?, ?)",
        (position_id, strategy, strategy, strategy, side, opened_ts,
         entry_atr_pct, entry_atr_timeframe),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, "
        " base_qty, fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, "
        " is_close, contribution_id, order_id, state) "
        "VALUES (?, ?, ?, ?, 'okx', ?, 0.001, ?, 80.0, 0.05, 1.0, 0.0, 0, "
        " ?, ?, 'filled')",
        (uuid.uuid4().hex, opened_ts * 1000, strategy, IID, side, entry_price,
         position_id, uuid.uuid4().hex),
    )


def _trade(position_id: str, strategy: str = "ema_crossover") -> SimulatedTrade:
    return SimulatedTrade(
        signal_id=uuid.uuid4().hex, venue="okx", symbol="BTC-USDT",
        strategy_id=strategy, side="long", entry_price=100.0,
        notional_usd=80.0, open_ts=NOW, position_id=position_id,
        correlation_group="crypto:BTC", underlying_group_id="crypto:BTC",
    )


def _regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "bull_trend"


async def _recalc(conn: sqlite3.Connection, state: ProdLoopState) -> int:
    return await recalc_active_positions(
        conn, state=state, now_ts=NOW,
        gpt_client=_MockGPTClient(['{"decision":"HOLD"}'] * 5), phase="P1",
        lookup_regime=_regime, close_specific=close_specific_position,
    )


def _row(conn: sqlite3.Connection, pid: str) -> dict[str, Any]:
    r = conn.execute(
        "SELECT status, stop_price, mfe_r, mae_r, exit_state FROM positions "
        "WHERE position_id = ?", (pid,),
    ).fetchone()
    return {"status": r[0], "stop_price": r[1], "mfe_r": r[2], "mae_r": r[3],
            "exit_state": r[4]}


# --- #1 regression: the 11-minute 1m-noise cut, reproduced then resolved ----


@pytest.mark.asyncio
async def test_1m_ruler_reproduces_noise_cut(memdb: sqlite3.Connection) -> None:
    """REPRODUCE: an unregistered (→1m ruler) position with tight 1m noise
    bars and a 0.5 dip is trail-stopped immediately — the pre-fix behaviour
    that cut 1H theses in ~11.7 minutes."""
    _seed_bars(memdb, interval="1m", n=20, band=0.05, close=99.5)
    _seed_bars(memdb, interval="1H", n=20, band=2.0, close=100.0,
               end_ts=NOW - 60)
    _seed_position(memdb, position_id="pos-1m-cut", strategy="vb")
    state = ProdLoopState()
    state.open_trades = [_trade("pos-1m-cut", strategy="vb")]
    await _recalc(memdb, state)
    assert _row(memdb, "pos-1m-cut")["status"] == "closed"
    assert state.recalc_precise_exit == 1


@pytest.mark.asyncio
async def test_1h_strategy_trail_uses_1h_atr(memdb: sqlite3.Connection) -> None:
    """RESOLVED: the SAME price action on an ema_crossover (1H) position survives —
    the trail uses the 1H ATR ruler, not the 1m ATR. ema_crossover is a TREND-bucket
    bar strategy so the let-winners-run width is the widened 3.0-ATR trail
    ([[ab_letrun_maker_2026-06-24]]). (Vehicle was spot_donchian; un-registered
    2026-06-27 #56 stop-bleeders KILL → ema_crossover is the live OKX 1H equivalent.)"""
    _seed_bars(memdb, interval="1m", n=20, band=0.05, close=99.5)
    _seed_bars(memdb, interval="1H", n=20, band=2.0, close=100.0,
               end_ts=NOW - 60)
    _seed_position(memdb, position_id="pos-1h", strategy="ema_crossover")
    state = ProdLoopState()
    state.open_trades = [_trade("pos-1h")]
    await _recalc(memdb, state)
    row = _row(memdb, "pos-1h")
    assert row["status"] == "open"  # the 1m noise dip no longer stops it
    # stop = peak(100) - 3.0 × (100 × 0.04) = 88.0 (the 1H ruler × the TREND trail).
    assert row["stop_price"] == pytest.approx(88.0, abs=0.5)
    # The 1H ATR landed in the recalc TTL cache (one query per cadence).
    assert (IID, "1H") in state.tf_atr_cache


@pytest.mark.asyncio
async def test_tick_engine_position_keeps_1m_ruler_via_bar_recalc(
    memdb: sqlite3.Connection,
) -> None:
    """Scalp-regression rail: a tick-engine position (micro_reversion, NOT in
    STRATEGY_REGISTRY) swept by the bar recalc still uses the 1m ruler —
    byte-identical pre-fix behaviour (closes on the same noise dip)."""
    _seed_bars(memdb, interval="1m", n=20, band=0.05, close=99.5)
    _seed_bars(memdb, interval="1H", n=20, band=2.0, close=100.0,
               end_ts=NOW - 60)
    _seed_position(memdb, position_id="pos-tick", strategy="micro_reversion")
    state = ProdLoopState()
    state.open_trades = [_trade("pos-tick", strategy="micro_reversion")]
    await _recalc(memdb, state)
    assert _row(memdb, "pos-tick")["status"] == "closed"
    assert state.recalc_precise_exit == 1


# --- #2: anchored denominator in the recalc row + legacy fallback -----------


@pytest.mark.asyncio
async def test_anchor_denominates_persisted_excursion(
    memdb: sqlite3.Connection,
) -> None:
    # Anchor 0.08 stamped at entry; current 1H ATR is 0.04 (contracted).
    # mae must be -0.5 / (100×0.08×2) = -0.03125 (anchor), NOT -0.0625.
    _seed_bars(memdb, interval="1m", n=20, band=0.05, close=99.5)
    _seed_bars(memdb, interval="1H", n=20, band=2.0, close=100.0,
               end_ts=NOW - 60)
    _seed_position(
        memdb, position_id="pos-anchor", strategy="ema_crossover",
        entry_atr_pct=0.08, entry_atr_timeframe="1H",
    )
    state = ProdLoopState()
    state.open_trades = [_trade("pos-anchor")]
    await _recalc(memdb, state)
    row = _row(memdb, "pos-anchor")
    assert row["status"] == "open"
    assert row["mae_r"] == pytest.approx(-0.5 / (100.0 * 0.08 * 2.0), rel=0.05)


@pytest.mark.asyncio
async def test_legacy_null_anchor_falls_back_to_current_tf_atr(
    memdb: sqlite3.Connection,
) -> None:
    # Legacy row (entry_atr_pct NULL) → denominator = CURRENT timeframe ATR
    # (0.04 on the 1H ruler): mae = -0.5 / (100×0.04×2) = -0.0625.
    _seed_bars(memdb, interval="1m", n=20, band=0.05, close=99.5)
    _seed_bars(memdb, interval="1H", n=20, band=2.0, close=100.0,
               end_ts=NOW - 60)
    _seed_position(memdb, position_id="pos-legacy", strategy="ema_crossover")
    state = ProdLoopState()
    state.open_trades = [_trade("pos-legacy")]
    await _recalc(memdb, state)
    row = _row(memdb, "pos-legacy")
    assert row["status"] == "open"
    assert row["mae_r"] == pytest.approx(-0.5 / (100.0 * 0.04 * 2.0), rel=0.05)


def test_load_rows_exposes_anchor_and_missing_flag(
    memdb: sqlite3.Connection,
) -> None:
    _seed_bars(memdb, interval="1m", n=20, band=0.05, close=100.0)
    _seed_position(
        memdb, position_id="pos-a", strategy="ema_crossover",
        entry_atr_pct=0.03, entry_atr_timeframe="1H",
    )
    _seed_position(memdb, position_id="pos-b", strategy="ema_crossover")
    rows = {
        r["position_id"]: r for r in load_active_position_rows(memdb)
    }
    assert rows["pos-a"]["entry_atr_pct"] == pytest.approx(0.03)
    assert rows["pos-a"]["anchor_missing"] is False
    assert rows["pos-b"]["entry_atr_pct"] is None
    assert rows["pos-b"]["anchor_missing"] is True


# --- open path stamps the anchor ---------------------------------------------


@pytest.mark.asyncio
async def test_open_path_stamps_entry_anchor(memdb: sqlite3.Connection) -> None:
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import reserve_and_submit
    from polaris.strategies.base import RawSignal

    reset_process_fence()
    _seed_bars(memdb, interval="1H", n=20, band=2.0, close=100.0)
    sig = RawSignal(
        signal_id="sig-anchor", strategy_id="ema_crossover", symbol="BTC-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_cross_sectional_momo",
    )
    state = ProdLoopState()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="okx", symbol="BTC-USDT",
        asset_class="crypto", underlying_group_id="crypto:BTC",
        notional_usd=80.0, last_price=100.0, now_ts=NOW,
    )
    assert trade is not None
    row = memdb.execute(
        "SELECT entry_atr_pct, entry_atr_timeframe FROM positions "
        "WHERE position_id = ?", (trade.position_id,),
    ).fetchone()
    assert row[0] == pytest.approx(0.04)
    assert row[1] == "1H"


@pytest.mark.asyncio
async def test_open_path_stamps_null_when_no_bars(
    memdb: sqlite3.Connection,
) -> None:
    """No bars at all → NULL anchor (never a fake 0.005), legacy-graceful."""
    from polaris.core.isolation.allocator_fence import reset_process_fence
    from polaris.scripts._production_pipeline import reserve_and_submit
    from polaris.strategies.base import RawSignal

    reset_process_fence()
    sig = RawSignal(
        signal_id="sig-nobars", strategy_id="ema_crossover", symbol="ETH-USDT",
        side="long", strength=0.8, sizing_hint=0.05, ttl_bars=10,
        thesis_tag="t", correlation_group="spot_cross_sectional_momo",
    )
    state = ProdLoopState()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue="okx", symbol="ETH-USDT",
        asset_class="crypto", underlying_group_id="crypto:ETH",
        notional_usd=80.0, last_price=100.0, now_ts=NOW,
    )
    assert trade is not None
    row = memdb.execute(
        "SELECT entry_atr_pct, entry_atr_timeframe FROM positions "
        "WHERE position_id = ?", (trade.position_id,),
    ).fetchone()
    assert row[0] is None
    assert row[1] is None


# --- close path shares the anchored denominator ------------------------------


def test_close_pnl_r_is_stream_common_not_atr_anchor(memdb: sqlite3.Connection) -> None:
    # Step N (2026-06-23): the realised-R denominator is the per-stream R_budget
    # (0.02 × OKX $79k = $1,580), NO LONGER the per-trade ATR anchor. The entry
    # fill is size_usd=80 @ entry 100, so exit 98 (long) → pnl_usd = -2/100*80 =
    # -$1.60 → pnl_r = -1.60 / 1580. The 0.04 anchor here no longer affects
    # realised R (it drives only the excursion mfe_r/mae_r path).
    _seed_bars(memdb, interval="1m", n=14, band=0.05, close=100.0)
    _seed_position(
        memdb, position_id="pos-close", strategy="ema_crossover",
        entry_atr_pct=0.04, entry_atr_timeframe="1H",
    )
    trade = _trade("pos-close")
    pnl_r, pnl_usd, exit_price = real_pnl_r_from_fills(
        memdb, trade=trade, exit_price_override=98.0,
    )
    assert exit_price == 98.0
    assert pnl_usd == pytest.approx(-1.60)
    assert pnl_r == pytest.approx(pnl_usd / r_budget_for_venue("okx"))


def test_close_pnl_r_independent_of_atr_anchor_presence(
    memdb: sqlite3.Connection,
) -> None:
    # Two positions, SAME $ outcome, one with an ATR anchor and one legacy-NULL:
    # the stream-common R is IDENTICAL (the anchor no longer touches realised R).
    _seed_bars(memdb, interval="1m", n=14, band=0.25, close=100.0)
    _seed_position(
        memdb, position_id="pos-anch", strategy="ema_crossover",
        entry_atr_pct=0.04, entry_atr_timeframe="1H",
    )
    _seed_position(memdb, position_id="pos-null", strategy="ema_crossover")
    r_anch, _u1, _e1 = real_pnl_r_from_fills(
        memdb, trade=_trade("pos-anch"), exit_price_override=99.0,
    )
    r_null, _u2, _e2 = real_pnl_r_from_fills(
        memdb, trade=_trade("pos-null"), exit_price_override=99.0,
    )
    # pnl_usd = -1/100*80 = -$0.80 → -0.80 / 1580, regardless of the anchor.
    assert r_anch == pytest.approx(-0.80 / r_budget_for_venue("okx"))
    assert r_anch == pytest.approx(r_null)


def test_close_excursion_uses_entry_anchor(memdb: sqlite3.Connection) -> None:
    _seed_bars(memdb, interval="1m", n=14, band=0.05, close=100.0)
    _seed_position(
        memdb, position_id="pos-exc", strategy="ema_crossover",
        entry_atr_pct=0.04, entry_atr_timeframe="1H",
    )
    memdb.execute(
        "UPDATE positions SET peak_price = 103.0, trough_price = 99.0 "
        "WHERE position_id = 'pos-exc'",
    )
    trade = _trade("pos-exc")
    trade.entry_price = 100.0
    mfe, mae, atr_risk_usd = _close_excursion_r(
        memdb, trade=trade, exit_price=100.0,
    )
    assert mfe == pytest.approx(3.0 / (100.0 * 0.04 * 2.0))
    assert mae == pytest.approx(-1.0 / (100.0 * 0.04 * 2.0))
    # whole-position 1R = per-unit atr_usd (100*0.04*2=8.0) × base_qty (0.001).
    assert atr_risk_usd == pytest.approx(8.0 * 0.001)
