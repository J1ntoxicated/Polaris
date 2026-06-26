"""BUILD_SCHEMA — close-time MFE/MAE persistence + pure excursion math.

Two layers:
1. Pure ``compute_mfe_r`` / ``compute_mae_r`` / ``compute_excursion_r`` — long
   and short are sign-symmetric; MFE >= 0, MAE <= 0; ATR floor keeps the result
   finite. Unit-tested with hand-computed R values.
2. The close path (``_close_trade_with_real_pnl`` via ``close_oldest_with_real_pnl``)
   persists final ``mfe_r`` / ``mae_r`` + ``exit_state='closed'`` on the
   positions row. Best-effort from tracked peak/trough vs entry — measurement
   only, never gates sizing / blocks entry.

DEMO/PAPER only. AGGRESSIVE bias preserved — this writes telemetry columns,
it does not throttle.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from polaris.core.live_recalc.excursion import (
    compute_excursion_r,
    compute_mae_r,
    compute_mfe_r,
)
from polaris.scripts._production_close import (
    _close_excursion_r,
    close_oldest_with_real_pnl,
)
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.storage.schema import init_db

# ---------------------------------------------------------------------------
# Pure excursion math
# ---------------------------------------------------------------------------


def test_mfe_mae_long() -> None:
    # entry 100, peak 110 (+10), trough 95 (-5), atr_usd 5 → MFE +2R, MAE -1R.
    mfe = compute_mfe_r(
        entry_price=100.0, peak_price=110.0, trough_price=95.0,
        side="long", atr_usd=5.0,
    )
    mae = compute_mae_r(
        entry_price=100.0, peak_price=110.0, trough_price=95.0,
        side="long", atr_usd=5.0,
    )
    assert mfe == pytest.approx(2.0)
    assert mae == pytest.approx(-1.0)


def test_mfe_mae_short() -> None:
    # SHORT entry 100, trough 90 (favourable +10), peak 105 (adverse +5),
    # atr_usd 5 → MFE +2R, MAE -1R (roles flip vs long).
    mfe = compute_mfe_r(
        entry_price=100.0, peak_price=105.0, trough_price=90.0,
        side="short", atr_usd=5.0,
    )
    mae = compute_mae_r(
        entry_price=100.0, peak_price=105.0, trough_price=90.0,
        side="short", atr_usd=5.0,
    )
    assert mfe == pytest.approx(2.0)
    assert mae == pytest.approx(-1.0)


def test_mfe_clamped_nonneg_mae_clamped_nonpos() -> None:
    # Long that only ever went against itself: peak==entry → MFE 0; trough below.
    mfe = compute_mfe_r(
        entry_price=100.0, peak_price=100.0, trough_price=90.0,
        side="long", atr_usd=5.0,
    )
    mae = compute_mae_r(
        entry_price=100.0, peak_price=100.0, trough_price=90.0,
        side="long", atr_usd=5.0,
    )
    assert mfe == 0.0
    assert mae == pytest.approx(-2.0)
    # Long that only ever went in favour: trough==entry → MAE 0.
    mae2 = compute_mae_r(
        entry_price=100.0, peak_price=110.0, trough_price=100.0,
        side="long", atr_usd=5.0,
    )
    assert mae2 == 0.0


def test_atr_floor_keeps_finite() -> None:
    # atr_usd 0 → floored to epsilon, result finite (huge but not inf/nan).
    mfe = compute_mfe_r(
        entry_price=100.0, peak_price=110.0, trough_price=100.0,
        side="long", atr_usd=0.0,
    )
    assert mfe > 0.0
    import math

    assert math.isfinite(mfe)


def test_compute_excursion_r_tuple() -> None:
    mfe, mae = compute_excursion_r(
        entry_price=100.0, peak_price=110.0, trough_price=95.0,
        side="long", atr_usd=5.0,
    )
    assert mfe == pytest.approx(2.0)
    assert mae == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Close-time write
# ---------------------------------------------------------------------------


def _memdb() -> sqlite3.Connection:
    return init_db(":memory:")


def _seed_entry(
    conn: sqlite3.Connection, *, position_id: str, side: str,
    entry_price: float, peak: float | None, trough: float | None,
) -> None:
    """Insert an open position + its entry fill so the close path can read it."""
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " peak_price, trough_price) "
        "VALUES (?, 'okx', 'BTC-USDT', 'volume_burst', 'volume_burst', "
        "        'volume_burst', ?, 0.001, 'open', ?, ?, ?)",
        (position_id, side, int(time.time()), peak, trough),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES (?, 'okx', 'okx:BTC-USDT', 'volume_burst', 'buy', "
        "        100.0, ?, 0.1, 1.0, ?, 'o1', ?, 0.0, 0, 0.001666, 100.0, 'filled')",
        (f"f_{position_id}", entry_price, int(time.time() * 1000), position_id),
    )
    conn.commit()


def _lookup_regime(_conn: sqlite3.Connection, _trade: object) -> str:
    return "chop"


def test_close_excursion_helper_uses_tracked_extremes() -> None:
    conn = _memdb()
    try:
        _seed_entry(
            conn, position_id="p1", side="long",
            entry_price=100.0, peak=112.0, trough=94.0,
        )
        trade = SimulatedTrade(
            signal_id="s1", venue="okx", symbol="BTC-USDT",
            strategy_id="volume_burst", side="long", entry_price=100.0,
            notional_usd=100.0, open_ts=int(time.time()), position_id="p1",
        )
        # No bars seeded → atr_pct fallback 0.005 → atr_usd = 100*0.005*2 = 1.0.
        mfe, mae, atr_risk_usd = _close_excursion_r(
            conn, trade=trade, exit_price=105.0,
        )
        # peak 112 vs entry 100 = +12 / 1.0 = +12R; trough 94 = -6 / 1.0 = -6R.
        assert mfe == pytest.approx(12.0)
        assert mae == pytest.approx(-6.0)
        # whole-position 1R = per-unit atr_usd (1.0) × entry base_qty (0.001666).
        assert atr_risk_usd == pytest.approx(1.0 * 0.001666)
    finally:
        conn.close()


def test_close_excursion_falls_back_to_exit_when_no_extremes() -> None:
    conn = _memdb()
    try:
        _seed_entry(
            conn, position_id="p2", side="long",
            entry_price=100.0, peak=None, trough=None,
        )
        trade = SimulatedTrade(
            signal_id="s2", venue="okx", symbol="BTC-USDT",
            strategy_id="volume_burst", side="long", entry_price=100.0,
            notional_usd=100.0, open_ts=int(time.time()), position_id="p2",
        )
        # atr_usd = 1.0. exit 103 above entry → MFE +3R, MAE 0 (never below entry).
        mfe, mae, atr_risk_usd = _close_excursion_r(
            conn, trade=trade, exit_price=103.0,
        )
        assert mfe == pytest.approx(3.0)
        assert mae == 0.0
        assert atr_risk_usd == pytest.approx(1.0 * 0.001666)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_close_persists_mfe_mae_and_exit_state() -> None:
    conn = _memdb()
    try:
        from polaris.scripts.production_paper_loop import ProdLoopState

        _seed_entry(
            conn, position_id="p3", side="long",
            entry_price=100.0, peak=110.0, trough=96.0,
        )
        state = ProdLoopState()
        trade = SimulatedTrade(
            signal_id="s3", venue="okx", symbol="BTC-USDT",
            strategy_id="volume_burst", side="long", entry_price=100.0,
            notional_usd=100.0, open_ts=int(time.time()), position_id="p3",
        )
        state.open_trades.append(trade)
        await close_oldest_with_real_pnl(
            conn, state=state, now_ts=int(time.time()),
            lookup_regime=_lookup_regime,
        )
        row = conn.execute(
            "SELECT status, exit_state, mfe_r, mae_r FROM positions "
            "WHERE position_id = 'p3'"
        ).fetchone()
        assert row[0] == "closed"
        assert row[1] == "closed"
        # atr_usd = 1.0; peak 110 = +10R, trough 96 = -4R.
        assert row[2] == pytest.approx(10.0)
        assert row[3] == pytest.approx(-4.0)
    finally:
        conn.close()
