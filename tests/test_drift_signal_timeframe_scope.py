"""Drift SIGNAL timeframe scope — review BLOCKER fix (DEMO/PAPER).

[[1d_exit_horizon_fix_2026-07-02]] follow-up. The prior fix timeframe-scaled
the drift materiality FLOOR (``EXIT_THESIS_DRIFT_FLOOR_RATIO`` /
``drift_floor_for_timeframe``) but a fresh Claude sub-agent review caught that
the drift SIGNAL itself (``momentum_drift``, sourced from
``load_active_position_rows``'s ``bar_row`` -> ``_recent_market_state`` ->
``_recent_tick_drift``) was STILL hardcoded to the last 20x1m bars for EVERY
strategy regardless of timeframe — only ``atr_pct`` had a genuine per-timeframe
override. A 1D floor (~11.2%) compared against a ~10-minute 1m drift can never
fire, silently disabling the entire uncorroborated momentum-drift CUT path for
every non-1m strategy. This suite proves:
  (a) ``load_active_position_rows`` now reads the drift-measurement bars on the
      ACTIVE STRATEGY's OWN timeframe (the missing counterpart to the existing
      ATR override), so ``momentum_drift`` and the floor are commensurate; and
  (b) the LIVE incident (index_dual_momentum_rotation, 1D, J225/AU200AU,
      thesis_cut at 48s hold) no longer reproduces end-to-end through
      ``recalc_active_positions`` with REAL bar-sourced (not hand-injected)
      drift.

Aggressive bias / flow_not_block intact: this is EXIT-TIMING precision only —
no size change, no entry block, no halt. The -1.0R hard rail and G6 crisis path
are untouched (proven separately by
``test_maturity_gate_never_touches_the_pnl_rail_layer`` in
``test_assess_thesis.py``).
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.scripts._production_close import close_specific_position
from polaris.scripts._production_recalc import (
    _recent_tick_drift,
    load_active_position_rows,
    recalc_active_positions,
)
from polaris.scripts._smoke_fills import SimulatedTrade
from polaris.scripts.production_paper_loop import ProdLoopState

NOW = 1_780_000_000
STRATEGY_1D = "index_dual_momentum_rotation"
IID = "okx:J225"
VENUE, SYMBOL = "okx", "J225"
ENTRY = 8_000.0


def _seed_1d_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    strategy_id: str,
    entry_price: float,
    last_1d_close: float,
    opened_ts: int,
) -> None:
    """Fresh position (just filled) + 20x1m bars (flat, near-zero drift) + 10x1D
    bars (real ~1.5% adverse drift, matching the live J225 incident magnitude)."""
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        " swap_count, exit_state, entry_atr_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'long', 1.0, 'open', ?, 0, 'open', ?)",
        (
            position_id, VENUE, SYMBOL, "index:AU", strategy_id, strategy_id,
            strategy_id, opened_ts, 0.01,
        ),
    )
    conn.execute(
        "INSERT INTO fills "
        "(fill_id, ts_ms, strategy_id, instrument_id, venue, side, base_qty, "
        " fill_price, size_usd, fee_usd, slippage_bps, pnl_usd, is_close, "
        " contribution_id, order_id, state) "
        "VALUES (?, ?, ?, ?, ?, 'long', 1.0, ?, 8000.0, 4.0, 1.0, 0.0, 0, ?, ?, "
        "        'filled')",
        (
            uuid.uuid4().hex, opened_ts * 1000, strategy_id, IID, VENUE,
            entry_price, position_id, uuid.uuid4().hex,
        ),
    )
    # 20x1m bars, essentially flat around entry (the fresh-fill intraday window
    # — real live behaviour: a 1D thesis fires seconds after fill, long before
    # any meaningful 1m drift accumulates).
    for i in range(20):
        ts = opened_ts - (20 - i) * 60
        c = entry_price
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, 'index:AU', ?, ?, '1m', ?, ?, ?, ?, ?, 10.0, ?, 1, ?, ?, "
            "        ?, 1.0, 'rest')",
            (IID, VENUE, SYMBOL, ts, c, c + 2.0, c - 2.0, c, c * 10.0, c, c, c),
        )
    # 10x1D bars: prior daily closes drifting from entry_price down to
    # last_1d_close (~1.5% adverse over the window) — a TYPICAL 1D bar-to-bar
    # span, well under the ~11.2% scaled 1D materiality floor.
    for i in range(10):
        ts = opened_ts - (10 - i) * 86_400
        frac = i / 9
        c = entry_price + (last_1d_close - entry_price) * frac
        conn.execute(
            "INSERT OR REPLACE INTO bars "
            "(instrument_id, underlying_group_id, venue, symbol, bar_interval, "
            " ts, open, high, low, close, volume, notional_usd, trade_count, "
            " vwap, bid_close, ask_close, spread_bps_close, source) "
            "VALUES (?, 'index:AU', ?, ?, '1D', ?, ?, ?, ?, ?, 10.0, ?, 1, ?, ?, "
            "        ?, 1.0, 'rest')",
            (IID, VENUE, SYMBOL, ts, c, c + 20.0, c - 20.0, c, c * 10.0, c, c, c),
        )


# --- (a) load_active_position_rows scopes the SIGNAL to the strategy's own tf ---


def test_1d_strategy_drift_signal_reads_1d_bars_not_1m(
    memdb: sqlite3.Connection,
) -> None:
    """The live incident's ROOT CAUSE: momentum_drift must reflect the 1D
    window (~-1.5%), NOT the near-flat 1m window (~0%) — else the timeframe-
    scaled floor (~11.2% for 1D) is compared against a signal that never
    scales and the CUT path is silently dead for every non-1m strategy."""
    pid = "pos-j225-signal"
    last_1d_close = ENTRY * 0.985  # -1.5% over the 1D window
    _seed_1d_position(
        memdb, position_id=pid, strategy_id=STRATEGY_1D, entry_price=ENTRY,
        last_1d_close=last_1d_close, opened_ts=NOW,
    )
    rows = load_active_position_rows(memdb, quote_writer=None)
    assert len(rows) == 1
    drift = _recent_tick_drift(rows[0]["recent_ticks"])
    # Must be the REAL 1D drift (~-1.5%), not the near-zero 1m drift.
    assert drift == pytest.approx(-0.015, abs=0.002)
    assert abs(drift) > 0.005  # far from the flat-1m near-zero signal


def test_1m_strategy_drift_signal_unchanged(memdb: sqlite3.Connection) -> None:
    """An unregistered / tick-engine strategy id ('1m' fallback) must keep
    reading the SAME 1m bar_row it always has — byte-identical."""
    pid = "pos-1m-signal"
    _seed_1d_position(
        memdb, position_id=pid, strategy_id="micro_reversion",
        entry_price=ENTRY, last_1d_close=ENTRY * 0.985, opened_ts=NOW,
    )
    rows = load_active_position_rows(memdb, quote_writer=None)
    assert len(rows) == 1
    drift = _recent_tick_drift(rows[0]["recent_ticks"])
    # 1m bars are flat at ENTRY in the fixture -> ~0 drift (the 1D drift must
    # NOT leak into a 1m-timeframe strategy's signal).
    assert drift == pytest.approx(0.0, abs=1e-9)


# --- (b) end-to-end: the LIVE J225/AU200AU 48s incident no longer reproduces ---


def _lookup_regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "trend"


@pytest.mark.asyncio
async def test_j225_incident_does_not_thesis_cut_at_48s_live_path(
    memdb: sqlite3.Connection,
) -> None:
    """Full ``recalc_active_positions`` reproduction of the 2026-07-02 02:52
    UTC live incident: index_dual_momentum_rotation (1D, horizon 21 bars),
    fresh fill, first recalc cycle ~48s later, typical -1.5% 1D drift, no OFI /
    no regime flip (uncorroborated momentum-only). Pre-fix this closed via
    thesis_cut on the FIRST recalc; post-fix (real bar-sourced signal, not a
    hand-injected one) it must stay open."""
    pid = "pos-j225-e2e"
    opened_ts = NOW
    _seed_1d_position(
        memdb, position_id=pid, strategy_id=STRATEGY_1D, entry_price=ENTRY,
        last_1d_close=ENTRY * 0.985, opened_ts=opened_ts,
    )
    state = ProdLoopState()
    state.open_trades = [
        SimulatedTrade(
            signal_id=uuid.uuid4().hex, venue=VENUE, symbol=SYMBOL,
            strategy_id=STRATEGY_1D, side="long", entry_price=ENTRY,
            notional_usd=8000.0, open_ts=opened_ts, position_id=pid,
            correlation_group="index:AU", underlying_group_id="index:AU",
        )
    ]
    await recalc_active_positions(
        memdb, state=state, now_ts=opened_ts + 48, gpt_client=None, phase="P0",
        lookup_regime=_lookup_regime, close_specific=close_specific_position,
    )
    row = memdb.execute(
        "SELECT status FROM positions WHERE position_id = ?", (pid,),
    ).fetchone()
    assert row is not None
    assert row[0] == "open"  # NOT thesis_cut — the live incident must not reproduce
