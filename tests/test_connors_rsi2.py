"""Connors RSI(2) Pullback — ultra-short oversold dip inside an uptrend.

Distinct from equity_rsi_bb_pullback (RSI(14)+Bollinger). This is RSI(2)+SMA(200):
fires only when the 2-period RSI is deeply oversold AND price is above its 200-SMA.
"""

from __future__ import annotations

from polaris.strategies import STRATEGY_REGISTRY, ConnorsRSI2Strategy
from polaris.strategies.base import BarView, MarketView
from polaris.strategies.connors_rsi2 import (
    REVERT_TARGET_R,
    TREND_FILTER_MA,
    _rsi,
    _sma,
)


def _bars_uptrend(n: int, *, start: float = 50.0, step: float = 0.2) -> list[BarView]:
    """A steadily rising series — close stays well above its 200-SMA."""
    base = 1_700_000_000
    out: list[BarView] = []
    for i in range(n):
        c = start + step * i
        out.append(
            BarView(ts=base + i * 86400, open=c, high=c + 0.1, low=c - 0.1,
                    close=c, volume=1000.0)
        )
    return out


def _mv(bars: list[BarView]) -> MarketView:
    return MarketView(
        symbol="AAPL", venue="alpaca", timeframe="1D",
        bars=bars, last_price=bars[-1].close, spread_bps=2.0, atr_pct=0.01,
    )


def test_fires_on_oversold_dip_in_uptrend() -> None:
    # Long rising base (close >> SMA200), then a sharp 2-bar drop → RSI(2) deeply
    # oversold while the last close is still above the 200-SMA. Canonical setup.
    bars = _bars_uptrend(220, start=50.0, step=0.3)  # last close ~115.7, SMA200 lower
    last_close = bars[-3].close
    drop = last_close - 1.5
    bars[-2] = BarView(ts=bars[-2].ts, open=drop, high=drop + 0.1, low=drop - 0.1,
                       close=drop, volume=1000.0)
    bars[-1] = BarView(ts=bars[-1].ts, open=drop - 1.5, high=drop, low=drop - 1.6,
                       close=drop - 1.5, volume=1000.0)
    mv = _mv(bars)
    # Confirm the canonical conditions actually hold.
    rsi2 = _rsi(bars, 2)
    sma200 = _sma(bars, TREND_FILTER_MA)
    assert rsi2 is not None and rsi2 < 10.0
    assert sma200 is not None and bars[-1].close > sma200
    sig = ConnorsRSI2Strategy().generate_raw_signal(mv)
    assert sig is not None
    assert sig.side == "long"
    assert sig.strategy_id == "connors_rsi2"
    assert 0.0 < sig.strength <= 1.0


def test_no_signal_when_not_oversold() -> None:
    # Steady uptrend, no dip → RSI(2) high → no entry (not noise-triggered).
    bars = _bars_uptrend(220, start=50.0, step=0.3)
    rsi2 = _rsi(bars, 2)
    assert rsi2 is not None and rsi2 >= 10.0
    assert ConnorsRSI2Strategy().generate_raw_signal(_mv(bars)) is None


def test_no_signal_when_below_sma200() -> None:
    # Deep oversold dip but in a DOWNTREND (close below SMA200) → trend filter
    # blocks the entry (no catching a falling knife).
    base = 1_700_000_000
    bars: list[BarView] = []
    for i in range(220):
        c = 200.0 - 0.5 * i  # steadily falling → close below its own SMA200
        bars.append(BarView(ts=base + i * 86400, open=c, high=c + 0.1, low=c - 0.1,
                            close=c, volume=1000.0))
    sma200 = _sma(bars, TREND_FILTER_MA)
    assert sma200 is not None and bars[-1].close <= sma200
    assert ConnorsRSI2Strategy().generate_raw_signal(_mv(bars)) is None


def test_no_signal_before_warmup() -> None:
    # Fewer than 200 bars → not enough history for the SMA200 trend filter.
    bars = _bars_uptrend(50)
    assert ConnorsRSI2Strategy().generate_raw_signal(_mv(bars)) is None


def test_metadata_opts_in_to_take_profit_target() -> None:
    # Mean-reversion → declares a fixed take-profit so the precise-exit engine
    # harvests the bounded revert (not let-winners-run).
    assert ConnorsRSI2Strategy.metadata.profit_target_r == REVERT_TARGET_R
    assert REVERT_TARGET_R == 1.0


def test_metadata_equity_long_only() -> None:
    md = ConnorsRSI2Strategy.metadata
    assert md.asset_class == "equity"
    assert md.timeframe == "1D"
    assert md.venue == "alpaca"


def test_registered_in_registry() -> None:
    assert STRATEGY_REGISTRY["connors_rsi2"] is ConnorsRSI2Strategy


def test_venue_guard_rejects_non_alpaca_market_view() -> None:
    # P1 fix (2 leaked Capital fills): connors_rsi2 is alpaca-only. A
    # market_view from ANY other venue must never emit, even given the exact
    # canonical oversold-dip-in-uptrend bar series that fires on alpaca.
    bars = _bars_uptrend(220, start=50.0, step=0.3)
    last_close = bars[-3].close
    drop = last_close - 1.5
    bars[-2] = BarView(ts=bars[-2].ts, open=drop, high=drop + 0.1, low=drop - 0.1,
                       close=drop, volume=1000.0)
    bars[-1] = BarView(ts=bars[-1].ts, open=drop - 1.5, high=drop, low=drop - 1.6,
                       close=drop - 1.5, volume=1000.0)
    capital_mv = MarketView(
        symbol="AAPL", venue="capital", timeframe="1D",
        bars=bars, last_price=bars[-1].close, spread_bps=2.0, atr_pct=0.01,
    )
    assert ConnorsRSI2Strategy().generate_raw_signal(capital_mv) is None
    # Sanity: the SAME bars on alpaca still fire (the guard is venue-only).
    assert ConnorsRSI2Strategy().generate_raw_signal(_mv(bars)) is not None


def test_harvest_helper_picks_up_target() -> None:
    from polaris.scripts._production_recalc_exit import _profit_target_for_strategy
    assert _profit_target_for_strategy("connors_rsi2") == 1.0


def test_rsi_full_loss_and_gain_conventions() -> None:
    base = 1_700_000_000
    up = [BarView(ts=base + i, open=10.0 + i, high=10.0 + i, low=10.0 + i,
                  close=10.0 + i, volume=1.0) for i in range(3)]
    assert _rsi(up, 2) == 100.0  # no downside → 100
    down = [BarView(ts=base + i, open=20.0 - i, high=20.0 - i, low=20.0 - i,
                    close=20.0 - i, volume=1.0) for i in range(3)]
    assert _rsi(down, 2) == 0.0  # no upside → 0
