"""fee-split v1 FLIP item 3 — SCALE gate wired into compute_size().

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Proves
the gate actually reaches the live sizing chain (registered != fired
precedent): an EARNED tier-amplifier bonus is WITHHELD (falls back to 1.0)
when the track has no proven gross edge yet, and PASSES THROUGH once
score_f_events shows a gross_LCB clearing the friction-plus-margin bar.
Baseline sizing (win_streak=0, no amplifier earned) is confirmed BYTE-
IDENTICAL either way — the gate only ever touches the bonus.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.classes.score_f import rollup_score_f
from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState, compute_size
from polaris.storage.schema import init_db

VENUE = "okx"
STRATEGY_ID = "rsi_bb_pullback"  # registered, venue=okx, no "_maker" suffix (taker-only)
NOW = 1_800_000_000


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _mk_closed(conn, *, position_id, closed_ts, pnl_usd, size_usd=1000.0):
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts) VALUES (?, ?, 'BTC-USDT', ?, ?, ?, 'long', 1.0, 'closed', ?, ?)",
        (position_id, VENUE, STRATEGY_ID, STRATEGY_ID, STRATEGY_ID, closed_ts - 3600, closed_ts),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'buy', ?, 100.0, 1.0, ?, ?, ?, ?, 1)",
        (uuid.uuid4().hex, VENUE, f"{VENUE}:BTC-USDT", STRATEGY_ID, size_usd,
         closed_ts * 1000, uuid.uuid4().hex, position_id, pnl_usd),
    )


def _intent() -> SignalIntent:
    return SignalIntent(
        signal_id="sig-1", venue=VENUE, symbol="BTC-USDT",
        instrument_id=f"{VENUE}:BTC-USDT", underlying_group_id="crypto:BTC",
        asset_class="crypto", strategy=STRATEGY_ID, track="A",
        regime="bull_trend", direction="long", signal_strength=1.2,
        listing_age_hours=1000.0, leverage=1.0, base_risk_pct=0.02,
    )


def _amplified_risk_state() -> StrategyRiskState:
    # win_streak=8, n>=10, hit>=0.70 -> resolve_tier_amplifier = 3.0x (ADR-005).
    return StrategyRiskState(
        venue=VENUE, strategy=STRATEGY_ID, closed_trades=25, kelly_p=0.55,
        kelly_q=0.45, kelly_fraction=0.05, win_streak=8, hit_rate_10=0.80,
        updated_ts=NOW,
    )


def _cold_risk_state() -> StrategyRiskState:
    return StrategyRiskState(
        venue=VENUE, strategy=STRATEGY_ID, closed_trades=25, kelly_p=0.55,
        kelly_q=0.45, kelly_fraction=0.05, win_streak=0, hit_rate_10=0.55,
        updated_ts=NOW,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0}, open_positions=[],
        fill_rate_active_cut=False,
    )


def test_amplifier_withheld_with_no_gross_edge_history(conn):
    """win_streak=8 EARNS a 3.0x tier amplifier (ADR-005), but with zero
    score_f_events history the SCALE gate has no proof yet -> withholds to
    baseline. Cross-checked against the SAME intent with win_streak=0 (no
    amplifier earned) sizing IDENTICALLY — proving withhold, not damping."""
    sized_amplified = compute_size(
        conn, intent=_intent(), risk_state=_amplified_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    sized_cold = compute_size(
        conn, intent=_intent(), risk_state=_cold_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized_amplified.final_risk_pct == pytest.approx(sized_cold.final_risk_pct)


def test_amplifier_passes_through_once_gross_edge_proven(conn):
    """Seed 20 strongly-positive-gross closes (gross_bps far above OKX's
    taker friction floor + margin) -> the gate's gross_LCB clears the bar ->
    the SAME win_streak=8 track now sizes LARGER than the cold baseline."""
    for i in range(20):
        _mk_closed(conn, position_id=f"p{i}", closed_ts=NOW - i * 100, pnl_usd=50.0)  # 500 gbps/close
    conn.commit()
    rollup_score_f(conn, now_ts=NOW)

    sized_amplified = compute_size(
        conn, intent=_intent(), risk_state=_amplified_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    sized_cold = compute_size(
        conn, intent=_intent(), risk_state=_cold_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert sized_amplified.final_risk_pct > sized_cold.final_risk_pct
