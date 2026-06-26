"""Emit-threshold relaxation — 11 EDGE entry thresholds widened (flow_not_block).

Jin "올려 빨리": raise the signal emit RATE by relaxing exactly 11 entry-trigger
thresholds ~25-35% (one conservative +20% on fx_range_fade's range max). Each
strategy is a SIGNAL GENERATOR (ADR-008); these tests pin, per threshold:

  * NEWLY-EMITS: an input that was BLOCKED at the OLD threshold but is INSIDE the
    relaxed band now emits a RawSignal — proves the relaxation took effect on the
    runtime read path (``self.<knob>`` defaults to the module constant).
  * STILL-NONE: an input still beyond the NEW relaxed band returns None — proves
    we did NOT make it emit-everything (no noise explosion).
  * THESIS/STRENGTH: the emitted side matches the strategy's thesis (long stays
    long, the fade short stays short) and strength is finite, in [floor, 1.0].

These are RED before the constant edits (old thresholds block the newly-emit
input) and GREEN after. No sizing chain / stop / R-rail is touched here.
"""

from __future__ import annotations

import math

from polaris.strategies import (
    CCIReversionStrategy,
    ConnorsRSI2Strategy,
    EMACrossoverStrategy,
    FXBreakoutBasketStrategy,
    RSIBBPullbackStrategy,
    SessionBreakoutStrategy,
    SpotDonchianStrategy,
)
from polaris.strategies.base import BarView, MarketView, RawSignal
from polaris.strategies.cci_reversion import _cci, _typical
from polaris.strategies.connors_rsi2 import TREND_FILTER_MA, _rsi, _sma

# fx_range_fade (strategy-wave1) and volume_burst (#61, 2026-06-27) were
# un-registered (KILLed); their modules are preserved so this relaxed-threshold
# unit test still exercises their entry logic.
from polaris.strategies.fx_range_fade import FXRangeFadeStrategy
from polaris.strategies.volume_burst import VolumeBurstStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bars(n: int, *, base_close: float, drift: float = 0.0,
          base_high: float = 0.0, vol: float = 1000.0) -> list[BarView]:
    """Mirror tests/test_strategies_signal_gen._make_bars (same shape)."""
    out: list[BarView] = []
    for i in range(n):
        c = base_close + i * drift
        out.append(
            BarView(
                ts=1_700_000_000 + i * 60,
                open=c - 0.5,
                high=max(c, base_high) + 0.5,
                low=c - 1.0,
                close=c,
                volume=vol,
                notional_usd=vol * c,
            )
        )
    return out


def _assert_strength_bounded(sig: RawSignal, floor: float) -> None:
    assert math.isfinite(sig.strength)
    assert floor <= sig.strength <= 1.0
    assert math.isfinite(sig.sizing_hint)


# ---------------------------------------------------------------------------
# 1. volume_burst — VOL_Z_THRESHOLD 2.5 -> 1.75
# ---------------------------------------------------------------------------


def _volume_burst_mv(volume_z: float) -> MarketView:
    bars = _bars(30, base_close=100.0, drift=0.05)
    # Clear-acceptance break (close decisively above prior_high) -> continuation long.
    bars[-1] = BarView(ts=bars[-1].ts + 60, open=101.0, high=110.0, low=100.5,
                       close=109.0, volume=5000.0, notional_usd=545000.0)
    return MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1m",
        bars=bars, last_price=109.0, spread_bps=2.0, atr_pct=0.001,
        volume_z=volume_z,
    )


def test_volume_burst_newly_emits_in_relaxed_z_band() -> None:
    # z=2.0 is below the OLD 2.5 floor (blocked) but above the NEW 1.75 floor.
    sig = VolumeBurstStrategy().generate_raw_signal(_volume_burst_mv(2.0))
    assert sig is not None
    assert sig.side == "long"  # clear-acceptance break = continuation, thesis-aligned
    _assert_strength_bounded(sig, 0.4)


def test_volume_burst_still_none_below_relaxed_z_band() -> None:
    # z=1.5 is below even the NEW 1.75 floor -> no emit (no noise explosion).
    assert VolumeBurstStrategy().generate_raw_signal(_volume_burst_mv(1.5)) is None


# ---------------------------------------------------------------------------
# 2. session_breakout — ATR_MULT 1.5 -> 1.05
# ---------------------------------------------------------------------------


def _session_breakout_mv(close: float) -> MarketView:
    bars = _bars(30, base_close=4500.0, drift=0.5)
    bars[-1] = BarView(ts=bars[-1].ts, open=4500.0, high=close + 2.0,
                       low=4499.0, close=close, volume=2000.0)
    return MarketView(
        symbol="US500", venue="capital", timeframe="5m",
        bars=bars, last_price=close, spread_bps=1.0,
        session_open_price=4500.0, session_atr=10.0,
        is_session_open_window=True,
    )


def test_session_breakout_newly_emits_in_relaxed_atr_band() -> None:
    # open+OLD(1.5*ATR)=4515; open+NEW(1.05*ATR)=4510.5. close=4513 sits between:
    # blocked at the old multiple, emits long at the relaxed one.
    sig = SessionBreakoutStrategy().generate_raw_signal(_session_breakout_mv(4513.0))
    assert sig is not None
    assert sig.side == "long"  # close above session_open band = breakout long, thesis-aligned
    _assert_strength_bounded(sig, 0.5)


def test_session_breakout_still_none_below_relaxed_atr_band() -> None:
    # close=4505 is below even open+NEW(1.05*ATR)=4510.5 -> no emit.
    assert SessionBreakoutStrategy().generate_raw_signal(_session_breakout_mv(4505.0)) is None


# ---------------------------------------------------------------------------
# 3. rsi_bb_pullback — RSI_THRESHOLD 30.0 -> 39.0
# ---------------------------------------------------------------------------


def _rsi_bb_mv(rsi: float) -> MarketView:
    bars = _bars(220, base_close=100.0, drift=0.10)
    last = bars[-1]
    bars[-1] = BarView(ts=last.ts, open=last.open, high=last.high,
                       low=85.0, close=88.0, volume=last.volume)
    return MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="15m",
        bars=bars, last_price=88.0, spread_bps=3.0,
        rsi_14=rsi, bb_lower=90.0, ma_200=80.0,
    )


def test_rsi_bb_pullback_newly_emits_in_relaxed_rsi_band() -> None:
    # rsi=35 is above the OLD 30 ceiling (blocked) but below the NEW 39 ceiling.
    sig = RSIBBPullbackStrategy().generate_raw_signal(_rsi_bb_mv(35.0))
    assert sig is not None
    assert sig.side == "long"  # dip-buy in uptrend = long, thesis-aligned
    _assert_strength_bounded(sig, 0.4)


def test_rsi_bb_pullback_still_none_above_relaxed_rsi_band() -> None:
    # rsi=42 is above even the NEW 39 ceiling -> no emit.
    assert RSIBBPullbackStrategy().generate_raw_signal(_rsi_bb_mv(42.0)) is None


# ---------------------------------------------------------------------------
# 4. cci_reversion — CCI_OVERSOLD -100.0 -> -70.0
# ---------------------------------------------------------------------------


def _cci_bar(ts: int, price: float) -> BarView:
    return BarView(ts=ts, open=price, high=price + 1.0, low=price - 1.0,
                   close=price, volume=1000.0)


def _cci_cross_bars(*, dip: float, recover: float, amp: float = 20.0) -> list[BarView]:
    """A mildly oscillating baseline (±``amp`` so the mean-deviation normaliser is
    non-trivial), a dip bar, then a recover bar — engineered so prev-bar CCI sits
    in (-100, -70] and current-bar CCI rises above -70 (the relaxed cross). A flat
    baseline cannot be used: a single outlier saturates CCI to ~-667 regardless of
    dip size, so the (-100,-70] band is unreachable without baseline variation."""
    base = 1_700_000_000
    bars = [
        _cci_bar(base + i * 3600, 2000.0 + (amp if i % 2 == 0 else -amp))
        for i in range(25)
    ]
    bars.append(_cci_bar(base + 25 * 3600, dip))
    bars.append(_cci_bar(base + 26 * 3600, recover))
    return bars


def _cci_mv(bars: list[BarView]) -> MarketView:
    return MarketView(
        symbol="GOLD", venue="capital", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=1.0, atr_pct=0.01,
    )


def test_cci_reversion_newly_emits_on_relaxed_cross_up() -> None:
    # A shallow dip whose prev-bar CCI lands in (-100, -70]: the OLD -100 cross
    # never triggers (never reached -100) but the NEW -70 cross-up does.
    bars = _cci_cross_bars(dip=1975.0, recover=2000.0)
    typicals = [_typical(b.high, b.low, b.close) for b in bars]
    cci_prev = _cci(typicals[:-1])
    cci_now = _cci(typicals)
    assert cci_prev is not None and cci_now is not None
    # Pin the engineered regime: prev in the relaxed-only band, now above -70.
    assert -100.0 < cci_prev <= -70.0
    assert cci_now > -70.0
    sig = CCIReversionStrategy().generate_raw_signal(_cci_mv(bars))
    assert sig is not None
    assert sig.side == "long"  # oversold revert = long-only, thesis-aligned
    _assert_strength_bounded(sig, 0.5)


def test_cci_reversion_still_none_when_no_relaxed_cross() -> None:
    # A trivial dip whose CCI never falls to -70 at all -> no cross -> no emit.
    bars = _cci_cross_bars(dip=1996.0, recover=2001.0)
    typicals = [_typical(b.high, b.low, b.close) for b in bars]
    cci_prev = _cci(typicals[:-1])
    assert cci_prev is not None and cci_prev > -70.0  # never reached the relaxed floor
    assert CCIReversionStrategy().generate_raw_signal(_cci_mv(bars)) is None


# ---------------------------------------------------------------------------
# 5. fx_range_fade — ADX_RANGE_MAX 25.0 -> 30.0 (conservative +20%)
# ---------------------------------------------------------------------------


def _fx_fade_bars(n: int, close: float) -> list[BarView]:
    base = 1_700_000_000
    return [
        BarView(ts=base + i * 3600, open=close, high=close + 0.001,
                low=close - 0.001, close=close, volume=1000.0)
        for i in range(n)
    ]


def _fx_fade_mv(close: float, *, adx: float) -> MarketView:
    # Price ABOVE the upper band (an up-extreme to fade).
    return MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=_fx_fade_bars(31, close), last_price=close, spread_bps=1.0, atr_pct=0.001,
        adx_14=adx, bb_upper=close - 0.0010, bb_lower=close - 0.0090,
        bb_middle=close - 0.0050,
    )


def test_fx_range_fade_newly_emits_in_relaxed_adx_band() -> None:
    # adx=28 is at/above the OLD 25 max (blocked) but below the NEW 30 max.
    sig = FXRangeFadeStrategy().generate_raw_signal(_fx_fade_mv(1.1050, adx=28.0))
    assert sig is not None
    assert sig.side == "short"  # fade the up-extreme back toward the mean, thesis-aligned
    _assert_strength_bounded(sig, 0.5)


def test_fx_range_fade_still_none_above_relaxed_adx_band() -> None:
    # adx=32 is above even the NEW 30 max (strong trend) -> fade yields, no emit.
    assert FXRangeFadeStrategy().generate_raw_signal(_fx_fade_mv(1.1050, adx=32.0)) is None


# ---------------------------------------------------------------------------
# 6. spot_donchian — ADX_THRESHOLD 20.0 -> 14.0
# ---------------------------------------------------------------------------


def _spot_donchian_mv(adx: float) -> MarketView:
    bars = _bars(60, base_close=100.0, drift=0.10)
    bars[-1] = BarView(ts=bars[-1].ts, open=110.0, high=115.0, low=109.0,
                       close=114.0, volume=1500.0)
    return MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=114.0, spread_bps=3.0,
        donchian_high_40=110.0, adx_14=adx,
    )


def test_spot_donchian_newly_emits_in_relaxed_adx_band() -> None:
    # adx=17 is below the OLD 20 floor (blocked) but above the NEW 14 floor.
    sig = SpotDonchianStrategy().generate_raw_signal(_spot_donchian_mv(17.0))
    assert sig is not None
    assert sig.side == "long"  # donchian breakout = long, thesis-aligned
    _assert_strength_bounded(sig, 0.5)


def test_spot_donchian_still_none_below_relaxed_adx_band() -> None:
    # adx=12 is below even the NEW 14 floor -> no emit.
    assert SpotDonchianStrategy().generate_raw_signal(_spot_donchian_mv(12.0)) is None


# ---------------------------------------------------------------------------
# 7. ema_crossover — ADX_THRESHOLD 20.0 -> 14.0
# ---------------------------------------------------------------------------


def _ema_cross_bars() -> list[BarView]:
    """Mirror tests/test_strategies_signal_gen._ema_cross_bars: a flat base then a
    single decisive up-bar that pulls the fast EMA UP through the slow EMA."""
    floor = 100.0
    closes: list[float] = [floor] * 60
    closes.append(floor + 8.0)
    out: list[BarView] = []
    for i, c in enumerate(closes):
        out.append(
            BarView(ts=1_700_000_000 + i * 3600, open=c - 0.3, high=c + 0.5,
                    low=c - 0.6, close=c, volume=1500.0, notional_usd=1500.0 * c)
        )
    return out


def _ema_cross_mv(adx: float) -> MarketView:
    bars = _ema_cross_bars()
    return MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=3.0,
        ma_200=100.0, adx_14=adx,
    )


def test_ema_crossover_newly_emits_in_relaxed_adx_band() -> None:
    # adx=17 is below the OLD 20 floor (blocked) but above the NEW 14 floor.
    sig = EMACrossoverStrategy().generate_raw_signal(_ema_cross_mv(17.0))
    assert sig is not None
    assert sig.side == "long"  # bull cross in uptrend = long-only, thesis-aligned
    _assert_strength_bounded(sig, 0.5)


def test_ema_crossover_still_none_below_relaxed_adx_band() -> None:
    # adx=12 is below even the NEW 14 floor (chop) -> no emit (whipsaw guard).
    assert EMACrossoverStrategy().generate_raw_signal(_ema_cross_mv(12.0)) is None


# ---------------------------------------------------------------------------
# 8. fx_breakout_basket — ADX_THRESHOLD 15.0 -> 10.5
# ---------------------------------------------------------------------------


def _fx_breakout_mv(adx: float) -> MarketView:
    bars = _bars(60, base_close=1.10, drift=0.001)
    bars[-1] = BarView(ts=bars[-1].ts, open=1.20, high=1.25, low=1.19,
                       close=1.24, volume=1500.0)
    return MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=bars, last_price=1.24, spread_bps=1.0,
        donchian_high_40=1.20, adx_14=adx,
    )


def test_fx_breakout_newly_emits_in_relaxed_adx_band() -> None:
    # adx=12 is below the OLD 15 floor (blocked) but above the NEW 10.5 floor.
    sig = FXBreakoutBasketStrategy().generate_raw_signal(_fx_breakout_mv(12.0))
    assert sig is not None
    assert sig.side == "long"  # donchian-high breakout = long, thesis-aligned
    assert sig.venue_constraints.get("leverage_max") == 30.0  # R-rail untouched
    _assert_strength_bounded(sig, 0.5)


def test_fx_breakout_still_none_below_relaxed_adx_band() -> None:
    # adx=9 is below even the NEW 10.5 floor -> no emit.
    assert FXBreakoutBasketStrategy().generate_raw_signal(_fx_breakout_mv(9.0)) is None


# ---------------------------------------------------------------------------
# 9. connors_rsi2 — RSI_ENTRY 10.0 -> 13.0
# (equity_rsi_bb_pullback + equity_gap_go relax-band tests removed — both
#  strategies KILLed 2026-06-26 for gross-negative entry expectancy.)
# ---------------------------------------------------------------------------


def _connors_bars(*, last_drop: float) -> list[BarView]:
    """Long rising base (close >> SMA200) then a final down-bar of magnitude
    ``last_drop`` from the prior close. The drop size sets RSI(2): a smaller drop
    after a steady up-leg lands RSI(2) in (10, 13]; a larger drop pushes it below 10.
    """
    base = 1_700_000_000
    out: list[BarView] = []
    start, step = 50.0, 0.3
    for i in range(219):
        c = start + step * i
        out.append(BarView(ts=base + i * 86400, open=c, high=c + 0.1,
                           low=c - 0.1, close=c, volume=1000.0))
    prev_close = out[-1].close
    final = prev_close - last_drop
    out.append(BarView(ts=base + 219 * 86400, open=final, high=final + 0.1,
                       low=final - 0.1, close=final, volume=1000.0))
    return out


def _connors_mv(bars: list[BarView]) -> MarketView:
    return MarketView(
        symbol="AAPL", venue="alpaca", timeframe="1D",
        bars=bars, last_price=bars[-1].close, spread_bps=2.0, atr_pct=0.01,
    )


def test_connors_rsi2_newly_emits_in_relaxed_rsi_band() -> None:
    # A modest final dip lands RSI(2) in (10, 13]: the OLD 10 entry blocks it but
    # the NEW 13 entry admits it, with the last close still above SMA200 (uptrend).
    bars = _connors_bars(last_drop=2.2)
    rsi2 = _rsi(bars, 2)
    sma200 = _sma(bars, TREND_FILTER_MA)
    assert rsi2 is not None and 10.0 < rsi2 < 13.0  # relaxed-only entry band
    assert sma200 is not None and bars[-1].close > sma200  # trend filter holds
    sig = ConnorsRSI2Strategy().generate_raw_signal(_connors_mv(bars))
    assert sig is not None
    assert sig.side == "long"  # oversold dip in uptrend = long-only, thesis-aligned
    _assert_strength_bounded(sig, 0.4)


def test_connors_rsi2_still_none_above_relaxed_rsi_band() -> None:
    # A trivial final dip keeps RSI(2) above the NEW 13 entry -> no emit.
    bars = _connors_bars(last_drop=0.3)
    rsi2 = _rsi(bars, 2)
    assert rsi2 is not None and rsi2 >= 13.0  # above the relaxed entry
    assert ConnorsRSI2Strategy().generate_raw_signal(_connors_mv(bars)) is None
