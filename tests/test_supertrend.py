"""Supertrend strategy — ATR-band trend-flip long-only trend follower on OKX SPOT.

Pins: fires on the canonical flip-UP setup, stays silent on a downtrend / a steady
uptrend (no flip this bar), respects the liquidity floor, opts OUT of a fixed
take-profit (trend ride — the band-flip-down is the natural exit), and is long-only.
"""

from __future__ import annotations

from polaris.strategies import STRATEGY_REGISTRY, SupertrendStrategy
from polaris.strategies.base import BarView, MarketView


def _bar(ts: int, o: float, h: float, low_: float, c: float) -> BarView:
    return BarView(ts=ts, open=o, high=h, low=low_, close=c, volume=1000.0)


def _mv(bars: list[BarView], *, atr_pct: float = 0.02) -> MarketView:
    return MarketView(
        symbol="BTC-USDT",
        venue="okx",
        timeframe="1H",
        bars=bars,
        last_price=bars[-1].close,
        spread_bps=1.0,
        atr_pct=atr_pct,
    )


def _downtrend_then_flip() -> list[BarView]:
    """A clean downtrend (establishing a high trailing upper band) then a single
    strong bar whose close vaults above the band → Supertrend flips UP."""
    base = 1_700_000_000
    bars: list[BarView] = []
    price = 1000.0
    # 25 descending bars: a sustained downtrend the Supertrend rides DOWN.
    for i in range(25):
        c = price
        bars.append(_bar(base + i * 3600, c + 4, c + 6, c - 6, c))
        price -= 8.0
    # The flip bar: a large bullish bar that closes far above the trailing band.
    last_close = bars[-1].close
    flip_close = last_close + 80.0
    bars.append(_bar(base + 25 * 3600, last_close, flip_close + 5, last_close - 2, flip_close))
    return bars


def test_fires_on_flip_up() -> None:
    sig = SupertrendStrategy().generate_raw_signal(_mv(_downtrend_then_flip()))
    assert sig is not None
    assert sig.side == "long"
    assert 0.0 < sig.strength <= 1.0
    assert sig.strategy_id == "supertrend"


def test_silent_in_steady_downtrend() -> None:
    # The downtrend alone (no flip bar) must NOT emit — trend stays DOWN.
    bars = _downtrend_then_flip()[:-1]
    assert SupertrendStrategy().generate_raw_signal(_mv(bars)) is None


def test_silent_in_steady_uptrend_no_flip() -> None:
    # A monotone uptrend: already UP for many bars, so there is no flip THIS bar.
    base = 1_700_000_000
    bars: list[BarView] = []
    price = 1000.0
    for i in range(30):
        c = price
        bars.append(_bar(base + i * 3600, c - 4, c + 6, c - 6, c))
        price += 8.0
    assert SupertrendStrategy().generate_raw_signal(_mv(bars)) is None


def test_no_double_fire_bar_after_flip() -> None:
    # The bar AFTER the flip (still uptrend, no new flip) must be silent.
    bars = _downtrend_then_flip()
    cont_close = bars[-1].close + 10.0
    bars.append(_bar(bars[-1].ts + 3600, bars[-1].close, cont_close + 3, bars[-1].close - 2, cont_close))
    assert SupertrendStrategy().generate_raw_signal(_mv(bars)) is None


def test_liquidity_floor_blocks_microcap() -> None:
    # A real flip but atr_pct below the floor → no-emit (liquid majors only).
    from polaris.strategies.supertrend import ATR_FLOOR_PCT

    mv = _mv(_downtrend_then_flip(), atr_pct=ATR_FLOOR_PCT / 2.0)
    assert SupertrendStrategy().generate_raw_signal(mv) is None


def test_warmup_guard() -> None:
    base = 1_700_000_000
    bars = [_bar(base + i * 3600, 1000, 1005, 995, 1000) for i in range(3)]
    assert SupertrendStrategy().generate_raw_signal(_mv(bars)) is None


def test_metadata_trend_ride_no_fixed_target() -> None:
    # Trend ride: the band IS the trailing stop, so NO fixed +R target — the
    # exit is the natural band-flip-down via the let-winners-run ATR-trail FSM.
    assert SupertrendStrategy.metadata.profit_target_r is None
    assert SupertrendStrategy.metadata.asset_class == "spot"
    assert SupertrendStrategy.metadata.venue == "okx"
    assert SupertrendStrategy.metadata.timeframe == "1H"


def test_registered() -> None:
    assert STRATEGY_REGISTRY["supertrend"] is SupertrendStrategy
