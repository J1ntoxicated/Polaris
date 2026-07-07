"""End-to-end proof: dispatch -> signal -> size -> open -> snapshot -> equity
-> weekly-trace is ONE connected chain under VIRTUAL ACCOUNT mode.

DEMO/PAPER ONLY (virtual funds, POLARIS_VIRTUAL_ACCOUNT=1, real_roundtrip
forced off — zero venue calls, sim fills only). Aggressive bias preserved —
this test proves REACHABILITY/wiring end-to-end, it adds no throttle.

Spawned per Jin's 2026-07-07 chain-verification mandate after the virtual-
trade lifecycle audit reported "chain fully connected, 0 signals = legitimate
quiet, not INERT". This test independently PROVES that verdict with a real
strategy (``gold_breakout_1h``, a genuine EARN-class dispatch-eligible
strategy) driven by a synthetic ascending-then-breakout 1H bar series — the
same fixture shape already used in ``test_strategy_gold_breakout_clones.py``
— through every real production link, asserting each of the 6 links in turn:

  L1  dispatch reachability  -- ``keep_on_bar_path`` says this Capital
      commodity symbol stays on the bar pipeline (CAPITAL_BAR_STRATEGY_SYMBOLS
      union), AND the strategy is in the live dispatch set
      (``_all_strategies()`` / ``metadata.dispatch_eligible``).
  L2  generate_raw_signal reached -- the real strategy object emits a
      non-None RawSignal from the synthetic breakout bars.
  L3  signal sized NON-zero (EARN) -- T4 ``compute_size`` on that RawSignal's
      strength produces a final_notional_usd > 0.
  L4  simulate_open (virtual) -- ``reserve_and_submit`` under virtual-forced
      real_roundtrip=False persists a ``positions`` row + entry fill.
  L5  position -> snapshot -- ``_read_positions`` (the dashboard SSOT reader)
      surfaces that exact position.
  L6  virtual_equity_now reflects unrealized -- once a fresh in-the-money
      bar mark exists for the open position, ``virtual_equity_now`` folds
      the live entry-vs-last unrealized P&L into equity (this test also
      pins the chain-audit fix: the prior implementation read a
      ``positions.upnl_usd`` column that never existed in the schema, so
      unrealized_pnl_usd was structurally always 0.0).
  L7  close -> weekly_equity_curve gets a row -- ``close_oldest_with_real_pnl``
      followed by ``all_current_week_rows`` shows the closed trade.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from polaris.core.isolation.allocator_fence import reset_process_fence
from polaris.core.sizing import PortfolioState, SignalIntent, StrategyRiskState, compute_size
from polaris.core.sizing.constants import CAPITAL_DEMO_STARTING_EQUITY_USD
from polaris.core.ticks.config import TICK_ENGINE_OWNED_VENUES
from polaris.scripts._production_close import close_oldest_with_real_pnl
from polaris.scripts._production_pipeline import reserve_and_submit
from polaris.scripts._production_tick import (
    CAPITAL_BAR_STRATEGY_SYMBOLS,
    _all_strategies,
    keep_on_bar_path,
)
from polaris.scripts._virtual_account import resolve_real_roundtrip
from polaris.scripts.dashboard.snapshot_q_positions import _read_positions
from polaris.scripts.production_paper_loop import ProdLoopState
from polaris.storage.virtual_account_equity import virtual_equity_now
from polaris.storage.weekly_equity_trace import all_current_week_rows, week_start_ts
from polaris.strategies import BarView, MarketView
from polaris.strategies.gold_breakout_1h import DONCHIAN_WINDOW, GoldBreakout1HStrategy

_HOUR = 3_600
_SYMBOL = "GOLD"
_VENUE = "capital"
_ASSET_CLASS = "commodity"  # the real Capital universe-row asset_class for GOLD


def _ascending_bars(n: int, *, base_close: float, drift: float) -> list[BarView]:
    """Synthetic ascending 1H bar series (mirrors test_strategy_gold_breakout_clones.py)."""
    out: list[BarView] = []
    for i in range(n):
        c = base_close + i * drift
        out.append(
            BarView(
                ts=1_700_000_000 + i * _HOUR,
                open=c - 0.2, high=c + 0.3, low=c - 0.4, close=c, volume=1_000.0,
            )
        )
    return out


def _force_breakout_last_bar(bars: list[BarView], *, window: int) -> list[BarView]:
    """Overwrite the final bar so it closes strictly above the prior N-bar high
    (excluding itself) -- a deterministic Donchian-55 breakout trigger."""
    prior_high = max(b.high for b in bars[-(window + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 5.0,
        low=last.low, close=prior_high + 4.0, volume=last.volume,
    )
    return bars


@pytest.mark.asyncio
async def test_virtual_trade_lifecycle_chain_end_to_end(
    memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_VIRTUAL_ACCOUNT", "1")
    reset_process_fence()
    now = int(time.time())

    # ---- L1: DISPATCH REACHABILITY ---------------------------------------
    # (a) the strategy's own symbol is on the bar path (union membership —
    # NOT vacated by the tick-engine asset-class skip).
    assert _VENUE in TICK_ENGINE_OWNED_VENUES
    assert keep_on_bar_path(asset_class=_ASSET_CLASS, symbol=_SYMBOL) is True
    assert _SYMBOL in CAPITAL_BAR_STRATEGY_SYMBOLS
    # (b) the strategy is actually in the LIVE dispatch set (dispatch_eligible
    # SSOT, not merely registered) -- this is what _run_tick iterates.
    dispatch_ids = {s.metadata.strategy_id for s in _all_strategies()}
    assert "gold_breakout_1h" in dispatch_ids

    # ---- L2: generate_raw_signal REACHED, emits a real RawSignal ---------
    strategy = GoldBreakout1HStrategy()
    bars = _ascending_bars(DONCHIAN_WINDOW + 10, base_close=2000.0, drift=0.5)
    bars = _force_breakout_last_bar(bars, window=DONCHIAN_WINDOW)
    mv = MarketView(
        symbol=_SYMBOL, venue=_VENUE, timeframe="1H", bars=bars,
        last_price=bars[-1].close, spread_bps=5.0,
    )
    sig = strategy.generate_raw_signal(mv)
    assert sig is not None
    assert sig.strategy_id == "gold_breakout_1h"
    assert sig.side == "long"
    assert 0.0 < sig.strength <= 1.0

    # ---- L3: SIZED NON-ZERO (T4, EARN path) ------------------------------
    intent = SignalIntent(
        signal_id=sig.signal_id, venue=_VENUE, symbol=_SYMBOL,
        instrument_id=f"{_VENUE}:{_SYMBOL}", underlying_group_id="commodity:GOLD",
        asset_class=_ASSET_CLASS, strategy=sig.strategy_id, track="B",
        regime="bull_trend", direction="long", signal_strength=sig.strength,
        listing_age_hours=999.0, leverage=20.0, base_risk_pct=0.02,
    )
    risk_state = StrategyRiskState(
        venue=_VENUE, strategy=sig.strategy_id, closed_trades=25,
        kelly_p=0.55, kelly_q=0.45, kelly_fraction=0.05, win_streak=0,
        hit_rate_10=0.55, updated_ts=now,
    )
    portfolio = PortfolioState(
        equity_usd=CAPITAL_DEMO_STARTING_EQUITY_USD, venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0, track_used_pct={"B": 0.0}, open_positions=[],
    )
    sized = compute_size(
        memdb, intent=intent, risk_state=risk_state, portfolio=portfolio, now_ts=now,
    )
    assert sized.final_notional_usd > 0.0

    # ---- L4: VIRTUAL OPEN persists a positions row + entry fill ----------
    effective_real_roundtrip = resolve_real_roundtrip(False)
    assert effective_real_roundtrip is False  # virtual mode forces sim fills
    state = ProdLoopState()
    trade = await reserve_and_submit(
        conn=memdb, state=state, sig=sig, venue=_VENUE, symbol=_SYMBOL,
        asset_class=_ASSET_CLASS, underlying_group_id="commodity:GOLD",
        notional_usd=sized.final_notional_usd, last_price=bars[-1].close,
        now_ts=now, real_roundtrip=effective_real_roundtrip,
    )
    assert trade is not None
    assert trade.notional_usd > 0.0
    state.open_trades.append(trade)

    pos_row = memdb.execute(
        "SELECT status, venue, symbol, strategy_id, side FROM positions WHERE position_id = ?",
        (trade.position_id,),
    ).fetchone()
    assert pos_row is not None
    assert pos_row[0] == "open"
    assert (pos_row[1], pos_row[2], pos_row[3], pos_row[4]) == (
        _VENUE, _SYMBOL, "gold_breakout_1h", "long",
    )
    # At minimum some fill row exists for this position's open leg.
    any_fill = memdb.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    assert any_fill >= 1

    # ---- L5: position -> SNAPSHOT read ------------------------------------
    positions = _read_positions(
        memdb, now_s=now, last_prices={f"{_VENUE}:{_SYMBOL}": bars[-1].close},
        entry_lookup={}, cell_mult={}, regime_lookup={},
    )
    matches = [
        p for p in positions
        if p.venue == _VENUE and p.symbol == _SYMBOL and p.strategy_id == "gold_breakout_1h"
    ]
    assert len(matches) == 1

    # ---- L6: virtual_equity_now reflects the open position's upnl --------
    # unrealized PnL is derived live from entry (fills.fill_price) vs current
    # price (latest `bars` close) via the SAME formula the dashboard board
    # uses (_read_positions) -- there is no stored positions.upnl_usd column
    # anywhere in the schema (chain-audit fix). Insert a fresh 1m bar priced
    # above the fill entry so the position is genuinely in-the-money, then
    # assert virtual_equity_now folds that unrealized gain into equity.
    entry_row = memdb.execute(
        "SELECT fill_price FROM fills WHERE is_close = 0 ORDER BY ts_ms DESC LIMIT 1"
    ).fetchone()
    assert entry_row is not None
    entry_price = float(entry_row[0])
    mark_price = entry_price + 10.0  # long position, price up -> positive upnl
    memdb.execute(
        "INSERT INTO bars (instrument_id, underlying_group_id, venue, symbol, "
        " bar_interval, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, '1m', ?, ?, ?, ?, ?, ?)",
        (f"{_VENUE}:{_SYMBOL}", "commodity:GOLD", _VENUE, _SYMBOL, now,
         mark_price, mark_price, mark_price, mark_price, 100.0),
    )
    veq_before_close = virtual_equity_now(
        memdb, exchange=_VENUE, seed_equity=CAPITAL_DEMO_STARTING_EQUITY_USD,
    )
    assert veq_before_close.unrealized_pnl_usd > 0.0
    assert veq_before_close.equity == pytest.approx(
        CAPITAL_DEMO_STARTING_EQUITY_USD + veq_before_close.unrealized_pnl_usd
    )

    # ---- L7: CLOSE -> weekly_equity_curve gets a row ----------------------
    def _lookup_regime(*_a: object, **_k: object) -> str:
        return "bull_trend"

    close_ts = now + 3_600
    await close_oldest_with_real_pnl(
        memdb, state=state, now_ts=close_ts, lookup_regime=_lookup_regime,
        real_roundtrip=effective_real_roundtrip,
    )
    assert len(state.closed_trades) == 1

    closed_row = memdb.execute(
        "SELECT status FROM positions WHERE position_id = ?", (trade.position_id,),
    ).fetchone()
    assert closed_row is not None and closed_row[0] == "closed"

    week = week_start_ts(close_ts)
    rows = all_current_week_rows(memdb, now_ts=close_ts)
    capital_rows = [r for r in rows if r.exchange == _VENUE]
    assert len(capital_rows) == 1
    assert capital_rows[0].week_start_ts == week
    assert capital_rows[0].trades == 1

    # Full-chain summary assertion: every one of the 6 links produced its
    # expected artifact from the SAME signal_id/position_id thread.
    assert sig.signal_id == trade.signal_id
