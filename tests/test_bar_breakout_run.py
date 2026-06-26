"""bar_breakout_run — daily Donchian-40 + ROC-10 breakout (TDD).

Spec source: research selection ``bar_breakout_run`` (family=bar_momentum_horizon,
NET +97.6bps verified, fee-beater rank-2). Signaling-strategy contract: the
strategy emits the ENTRY trigger only; the asymmetric let-winners-run exit
(initial hard stop -2.5*ATR + ratcheting ATR-trail + daily-scale time backstop)
is owned by the G5/G7 gates via StrategyMetadata + the production exit FSM.

Tests assert:
  * ENTRY trigger: Donchian-40 prior-high breakout AND ROC-10 > 0 (both required).
  * flow_not_block: a clean trigger ALWAYS emits (no defensive entry block).
  * No look-ahead: the breakout test excludes the current closing bar.
  * Metadata wiring: timeframe=1D (daily/position horizon), TREND exit bucket
    (let-winners-run — correlation_group has no reversion substring),
    hold_overnight=True (multi-day swing), profit_target_r is None (no fixed
    take-profit — winners run), named Final params Donchian-40 / ROC-10.
  * Registry: registered in STRATEGY_REGISTRY under its strategy_id.
  * Full backtest (replay): NET expectancy beats the round-trip fee on a
    synthetic uptrending daily series (exit modeling + slippage + real fees).
"""

from __future__ import annotations

import sqlite3

from polaris.core.data.schema import Bar
from polaris.core.replay.engine import ReplayEngine
from polaris.core.replay.models import ReplayConfig
from polaris.storage.schema import ALL_DDL
from polaris.strategies import STRATEGY_REGISTRY, BarView, MarketView
from polaris.strategies.bar_breakout_run import (
    DONCHIAN_WINDOW,
    ROC_LOOKBACK,
    BarBreakoutRunStrategy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DAY = 86_400


def _daily_bars(
    n: int, *, base_close: float, drift: float, vol: float = 1000.0
) -> list[BarView]:
    """``n`` daily BarViews with a linear ``drift`` per bar (newest last)."""
    out: list[BarView] = []
    for i in range(n):
        c = base_close + i * drift
        out.append(
            BarView(
                ts=1_700_000_000 + i * _DAY,
                open=c - 0.2,
                high=c + 0.3,
                low=c - 0.4,
                close=c,
                volume=vol,
                notional_usd=vol * c,
            )
        )
    return out


def _mv(bars: list[BarView], *, donchian_high_40: float | None) -> MarketView:
    return MarketView(
        symbol="BTC-USDT",
        venue="okx",
        timeframe="1D",
        bars=bars,
        last_price=bars[-1].close,
        spread_bps=4.0,
        donchian_high_40=donchian_high_40,
    )


# ---------------------------------------------------------------------------
# ENTRY trigger
# ---------------------------------------------------------------------------


def test_emits_long_on_donchian_breakout_with_positive_roc() -> None:
    s = BarBreakoutRunStrategy()
    # 60 rising bars (> warmup=51) → prior-40 high known, ROC-10 positive; last
    # bar breaks out.
    bars = _daily_bars(60, base_close=100.0, drift=0.5)
    prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
    last = bars[-1]
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 5.0,
        low=last.low, close=prior_high + 4.0, volume=last.volume,
    )
    sig = s.generate_raw_signal(_mv(bars, donchian_high_40=prior_high))
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "bar_breakout_run"
    assert sig.correlation_group == "bar_momentum_breakout"


def test_no_emit_when_no_breakout() -> None:
    s = BarBreakoutRunStrategy()
    bars = _daily_bars(60, base_close=100.0, drift=0.5)
    prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
    last = bars[-1]
    # close BELOW the prior-40 high → no breakout.
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high - 1.0,
        low=last.low, close=prior_high - 2.0, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, donchian_high_40=prior_high)) is None


def test_no_emit_when_roc_not_positive_despite_breakout() -> None:
    s = BarBreakoutRunStrategy()
    # 50 rising bars then 11 FALLING bars → close[-1] < close[-11] (ROC-10 < 0).
    # Pass a low ``donchian_high_40`` so the BREAKOUT condition is satisfied,
    # isolating the ROC gate as the sole discriminator (both required → no emit).
    bars = _daily_bars(50, base_close=100.0, drift=0.5)
    falling_start = bars[-1].close
    for i in range(1, 12):
        c = falling_start - i * 0.3
        bars.append(
            BarView(
                ts=bars[-1].ts + _DAY, open=c, high=c + 0.2, low=c - 0.2,
                close=c, volume=1000.0,
            )
        )
    roc = bars[-1].close / bars[-(ROC_LOOKBACK + 1)].close - 1.0
    assert roc < 0.0  # fixture sanity: ROC gate is the discriminator
    # donchian_high_40 below last close → breakout passes; ROC<0 → still no emit.
    low_prior_high = bars[-1].close - 1.0
    assert (
        s.generate_raw_signal(_mv(bars, donchian_high_40=low_prior_high)) is None
    )


def test_no_lookahead_breakout_excludes_current_bar() -> None:
    # The current closing bar's own high must NOT be part of the Donchian window
    # (else a bar always "breaks" its own high). Feed donchian_high_40=None so
    # the strategy recomputes from bars[-(W+1):-1] and prove the exclusion.
    s = BarBreakoutRunStrategy()
    bars = _daily_bars(50, base_close=100.0, drift=0.5)
    prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
    last = bars[-1]
    # close EQUALS the prior high (no strict break) but the bar's OWN high is
    # higher — a look-ahead bug would treat this as a breakout.
    bars[-1] = BarView(
        ts=last.ts, open=last.open, high=prior_high + 10.0,
        low=last.low, close=prior_high, volume=last.volume,
    )
    assert s.generate_raw_signal(_mv(bars, donchian_high_40=None)) is None


def test_warmup_guard_short_history() -> None:
    s = BarBreakoutRunStrategy()
    bars = _daily_bars(10, base_close=100.0, drift=0.5)
    assert s.generate_raw_signal(_mv(bars, donchian_high_40=None)) is None


# ---------------------------------------------------------------------------
# Metadata wiring (exit / horizon ownership lives in G5/G7 via metadata)
# ---------------------------------------------------------------------------


def test_metadata_daily_position_horizon() -> None:
    meta = BarBreakoutRunStrategy.metadata
    assert meta.timeframe == "1D"  # daily/position horizon (#15 multi-horizon)
    assert meta.venue == "okx"
    assert meta.hold_overnight is True  # multi-day swing — not EOD-flattened
    assert meta.profit_target_r is None  # let winners run (no fixed take-profit)


def test_exit_bucket_is_trend_let_run() -> None:
    # correlation_group must NOT carry a reversion substring → TREND archetype
    # (let-winners-run), so the spec's ATR-trail let-run exit is honored by G7.
    from polaris.core.live_recalc.exit_engine import (
        Bucket,
        bucket_from_correlation_group,
    )

    cg = BarBreakoutRunStrategy.metadata.correlation_group_id
    assert bucket_from_correlation_group(cg) is Bucket.TREND


def test_named_final_verification_params() -> None:
    # The verified parameters are named Final constants (no magic numbers).
    assert DONCHIAN_WINDOW == 40
    assert ROC_LOOKBACK == 10


def test_registered_in_registry() -> None:
    assert STRATEGY_REGISTRY.get("bar_breakout_run") is BarBreakoutRunStrategy


# ---------------------------------------------------------------------------
# Full backtest — NET beats fee (replay engine: slippage + real round-trip fee)
# ---------------------------------------------------------------------------


def _canonical_daily_bars(
    symbol: str, *, n: int, base: float, drift: float
) -> list[Bar]:
    out: list[Bar] = []
    for i in range(n):
        c = base + i * drift
        out.append(
            Bar(
                instrument_id=f"okx:{symbol}",
                underlying_group_id=f"crypto:{symbol.split('-')[0]}",
                venue="okx",
                symbol=symbol,
                bar_interval="1D",
                ts=1_700_000_000 + i * _DAY,
                open=c - 0.2,
                high=c + 0.6,
                low=c - 0.6,
                close=c,
                volume=1000.0,
                spread_bps_close=2.0,
            )
        )
    return out


def test_full_backtest_net_beats_fee() -> None:
    # A long, persistent daily uptrend: the breakout fires once, the let-run
    # ATR-trail rides the trend, and the realized NET (after slippage + real
    # round-trip fee) is positive — i.e. the strategy clears its own fee.
    bars = _canonical_daily_bars("BTC-USDT", n=120, base=100.0, drift=1.0)
    sandbox = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        sandbox.execute(stmt)
    try:
        engine = ReplayEngine(
            ReplayConfig(
                instrument_ids=("okx:BTC-USDT",),
                live_db_path=":memory:",
                bar_interval="1D",
                starting_equity=100_000.0,
            ),
            strategies=[BarBreakoutRunStrategy()],
        )
        result = engine.run_with_bars(sandbox, {"okx:BTC-USDT": bars})
    finally:
        sandbox.close()
    trades = [t for t in result.trades if t.strategy == "bar_breakout_run"]
    assert trades, "strategy must take at least one trade on a clean uptrend"
    total_net = sum(t.net_pnl_usd for t in trades)
    total_fee = sum(t.rt_fee_usd for t in trades)
    assert total_net > 0.0  # NET (after fee) positive — beats the fee
    assert total_fee > 0.0  # sanity: a real round-trip fee was charged
    # NET expectancy in bps of notional must be positive (fee-beater).
    net_bps = 10_000.0 * total_net / sum(abs(t.notional) for t in trades)
    assert net_bps > 0.0
