"""FX Range Fade strategy — mean-reversion in low-ADX ranges, with the ADX trend
guard that keeps it from fading a trending tape (the design-review requirement).
"""

from __future__ import annotations

from polaris.strategies import FXRangeFadeStrategy
from polaris.strategies.base import BarView, MarketView


def _bars(n: int, close: float) -> list[BarView]:
    base = 1_700_000_000
    return [
        BarView(
            ts=base + i * 3600, open=close, high=close + 0.001,
            low=close - 0.001, close=close, volume=1000.0,
        )
        for i in range(n)
    ]


def _mv(
    symbol: str, close: float, *, adx: float,
    bb_upper: float, bb_lower: float, bb_middle: float,
) -> MarketView:
    return MarketView(
        symbol=symbol, venue="capital", timeframe="1H",
        bars=_bars(31, close), last_price=close, spread_bps=1.0, atr_pct=0.001,
        adx_14=adx, bb_upper=bb_upper, bb_lower=bb_lower, bb_middle=bb_middle,
    )


def test_fade_short_at_upper_band_in_range() -> None:
    mv = _mv("EURUSD", 1.1050, adx=12.0, bb_upper=1.1040, bb_lower=1.0960, bb_middle=1.1000)
    sig = FXRangeFadeStrategy().generate_raw_signal(mv)
    assert sig is not None and sig.side == "short"
    assert 0.0 < sig.strength <= 1.0


def test_fade_long_at_lower_band_in_range() -> None:
    mv = _mv("GBPUSD", 1.2550, adx=10.0, bb_upper=1.2640, bb_lower=1.2560, bb_middle=1.2600)
    sig = FXRangeFadeStrategy().generate_raw_signal(mv)
    assert sig is not None and sig.side == "long"


def test_no_fade_in_trend_high_adx() -> None:
    # At the upper band but ADX is high (trend) → yield to fx_breakout_basket.
    mv = _mv("EURUSD", 1.1050, adx=28.0, bb_upper=1.1040, bb_lower=1.0960, bb_middle=1.1000)
    assert FXRangeFadeStrategy().generate_raw_signal(mv) is None


def test_no_fade_inside_bands() -> None:
    mv = _mv("EURUSD", 1.1000, adx=12.0, bb_upper=1.1040, bb_lower=1.0960, bb_middle=1.1000)
    assert FXRangeFadeStrategy().generate_raw_signal(mv) is None


def test_no_signal_for_non_fx_symbol() -> None:
    mv = _mv("GOLD", 2000.0, adx=12.0, bb_upper=1990.0, bb_lower=1950.0, bb_middle=1970.0)
    assert FXRangeFadeStrategy().generate_raw_signal(mv) is None


def test_disjoint_from_breakout_adx_band() -> None:
    # fx_breakout fires adx>20; fade fires adx<20 — same bar can't trigger both.
    from polaris.strategies.fx_breakout_basket import ADX_THRESHOLD
    from polaris.strategies.fx_range_fade import ADX_RANGE_MAX
    assert ADX_RANGE_MAX <= ADX_THRESHOLD


def test_capital_epic_suffix_normalizes() -> None:
    # AUDUSD_ZERO (Capital epic) must normalize into the basket.
    mv = _mv("AUDUSD_ZERO", 0.6450, adx=11.0, bb_upper=0.6480, bb_lower=0.6460, bb_middle=0.6470)
    sig = FXRangeFadeStrategy().generate_raw_signal(mv)
    assert sig is not None and sig.side == "long"


def test_metadata_opts_in_to_take_profit_target() -> None:
    # The fade declares a fixed take-profit so the precise-exit engine harvests
    # the revert-to-mean instead of letting the wide ATR trail give it back.
    from polaris.strategies.fx_range_fade import FADE_TARGET_R
    assert FXRangeFadeStrategy.metadata.profit_target_r == FADE_TARGET_R
    assert FADE_TARGET_R == 1.0


def test_profit_target_helper_fade_vs_momentum() -> None:
    from polaris.scripts._production_recalc_exit import _profit_target_for_strategy
    assert _profit_target_for_strategy("fx_range_fade") == 1.0
    # A momentum strategy opts out → None → let-winners-run (trend exit) unchanged.
    assert _profit_target_for_strategy("tsmom") is None
    assert _profit_target_for_strategy("does_not_exist") is None
