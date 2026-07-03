"""End-to-end wiring test (pts-classes group WIRE) — the mission's required
proof: a fake close event -> score_F update -> transition -> the NEXT
``compute_size`` call reflects the new class.

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
Aggressive bias preserved: this test proves capital ROUTING re-wires after a
harsh loss streak (EARN -> BENCH), never that the strategy is blocked — the
strategy still signals/computes fully (BENCH shadow-routes, it does not
vanish). Registered≠fired precedent (this session's 8 prior silent-INERT
incidents) means the test asserts the FULL chain fires end-to-end, not just
that each piece independently works in isolation.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

import pytest

from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState, compute_size
from polaris.core.sizing.probe_notional import resolve_strategy_class
from polaris.scripts._production_close_classes import update_strategy_class_on_close
from polaris.storage.schema import init_db

VENUE = "capital"
STRATEGY_ID = "gold_riskoff_trend_amplify"  # registered, venue=capital
NOW = 1_780_000_000


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


@dataclass
class _Trade:
    venue: str
    strategy_id: str


def _seed_earn(conn: sqlite3.Connection, *, window_w: int = 3) -> None:
    conn.execute(
        "INSERT INTO strategy_class (venue, strategy_id, strategy_class, "
        "window_w, dwell, epoch_id, last_transition_ts, kill_state) "
        "VALUES (?, ?, 'EARN', ?, 0, 1, ?, 'ACTIVE')",
        (VENUE, STRATEGY_ID, window_w, NOW - 100_000),
    )
    conn.commit()


def _mk_losing_close(conn: sqlite3.Connection, *, position_id: str, closed_ts: int) -> None:
    """A closed lifecycle with a harsh loss (pnl_r=-5.0, net negative)."""
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "closed_ts, pnl_r) VALUES (?, ?, 'XAUUSD', ?, ?, ?, 'long', 1.0, "
        "'closed', ?, ?, -5.0)",
        (position_id, VENUE, STRATEGY_ID, STRATEGY_ID, STRATEGY_ID,
         closed_ts - 3600, closed_ts),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'buy', 100.0, 100.0, 1.0, ?, "
        "?, ?, 0.0, 0)",
        (uuid.uuid4().hex, VENUE, f"{VENUE}:XAUUSD", STRATEGY_ID,
         (closed_ts - 3600) * 1000, uuid.uuid4().hex, position_id),
    )
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, fee_usd, ts_ms, order_id, contribution_id, "
        "pnl_usd, is_close) VALUES (?, ?, ?, ?, 'sell', 100.0, 100.0, 1.0, ?, "
        "?, ?, -500.0, 1)",
        (uuid.uuid4().hex, VENUE, f"{VENUE}:XAUUSD", STRATEGY_ID,
         closed_ts * 1000, uuid.uuid4().hex, position_id),
    )
    conn.commit()


def _intent(*, strategy_class: str) -> SignalIntent:
    return SignalIntent(
        signal_id="sig-1", venue=VENUE, symbol="XAUUSD",
        instrument_id=f"{VENUE}:XAUUSD", underlying_group_id="metal:XAU",
        asset_class="metal", strategy=STRATEGY_ID, track="A",
        regime="bull_trend", direction="long", signal_strength=1.2,
        listing_age_hours=1000.0, leverage=1.0, base_risk_pct=0.02,
        strategy_class=strategy_class, atr_pct=0.02, stop_atr_mult=2.0,
    )


def _risk_state() -> StrategyRiskState:
    return StrategyRiskState(
        venue=VENUE, strategy=STRATEGY_ID, closed_trades=25, kelly_p=0.55,
        kelly_q=0.45, kelly_fraction=0.05, win_streak=2, hit_rate_10=0.55,
        updated_ts=NOW,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        equity_usd=10_000.0, venue_daily_used_pct=0.0, total_daily_used_pct=0.0,
        track_used_pct={"A": 0.0, "B": 0.0}, open_positions=[],
        fill_rate_active_cut=False,
    )


def test_fake_close_event_drives_score_transition_and_next_sizing(conn):
    """The FULL chain: close event -> score_F rollup -> transition ->
    strategy_class UPDATE -> next resolve_strategy_class read -> next
    compute_size call is sized as BENCH (shadow, notional=0), not EARN."""
    _seed_earn(conn, window_w=3)

    # Sanity: BEFORE any close event, this track sizes as EARN (full T4 chain).
    pre_class = resolve_strategy_class(conn, venue=VENUE, strategy_id=STRATEGY_ID)
    assert pre_class == "EARN"
    pre_sized = compute_size(
        conn, intent=_intent(strategy_class=pre_class), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert pre_sized.final_notional_usd > 0.0
    assert pre_sized.binding_cap != "bench_shadow"

    # Fake 3 harsh-loss close events firing the REAL close hook (the wiring
    # under test — not a direct call into score_f.py/transition.py).
    for i in range(3):
        ts = NOW - 3 * 3600 + i * 3600
        _mk_losing_close(conn, position_id=f"p{i}", closed_ts=ts)
        update_strategy_class_on_close(
            conn, trade=_Trade(venue=VENUE, strategy_id=STRATEGY_ID),
            now_ts=ts, state=object(),
        )

    # The transition fired: strategy_class flipped EARN -> BENCH.
    post_class = resolve_strategy_class(conn, venue=VENUE, strategy_id=STRATEGY_ID)
    assert post_class == "BENCH"

    # The NEXT compute_size call (using the freshly-read class, exactly as
    # entry_sizer_gate/replay/tick_engine do in production) reflects the new
    # class: shadow-routed, notional forced to 0 — capital ROUTING, not a
    # block (the signal is still fully computed above in compute_size).
    post_sized = compute_size(
        conn, intent=_intent(strategy_class=post_class), risk_state=_risk_state(),
        portfolio=_portfolio(), now_ts=NOW,
    )
    assert post_sized.final_notional_usd == 0.0
    assert post_sized.binding_cap == "bench_shadow"

    # And the shadow fill was actually recorded into shadow_ring by this same
    # compute_size call (item 5 of the wiring scope) — proving BENCH routing
    # is not a silent-INERT dead end either.
    ring_row = conn.execute(
        "SELECT shadow_ring FROM strategy_class WHERE venue=? AND strategy_id=?",
        (VENUE, STRATEGY_ID),
    ).fetchone()
    assert ring_row[0] != "[]"
