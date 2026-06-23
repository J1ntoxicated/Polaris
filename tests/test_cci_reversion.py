"""CCI(20) Oversold Reversion — Capital commodity/index CFD mean-reversion.

Fires when CCI(20) crosses back UP through -100 (statistically-extreme oversold
deviation reverting). LONG-ONLY. Declares profit_target_r so the precise-exit
engine harvests the bounded revert-to-mean.
"""

from __future__ import annotations

from polaris.strategies import CCIReversionStrategy
from polaris.strategies.base import BarView, MarketView
from polaris.strategies.cci_reversion import CCI_WINDOW, _cci, _typical


def _mv(symbol: str, bars: list[BarView]) -> MarketView:
    return MarketView(
        symbol=symbol, venue="capital", timeframe="1H",
        bars=bars, last_price=bars[-1].close, spread_bps=1.0, atr_pct=0.01,
    )


def _bar(ts: int, price: float, *, spread: float = 1.0) -> BarView:
    return BarView(
        ts=ts, open=price, high=price + spread, low=price - spread,
        close=price, volume=1000.0,
    )


def _oversold_then_revert(symbol: str = "GOLD") -> list[BarView]:
    """A flat-ish baseline, a sharp dip (CCI <= -100), then a recovery bar that
    pulls CCI back above -100 — the canonical cross-up setup.
    """
    base = 1_700_000_000
    bars: list[BarView] = []
    # 25 flat baseline bars around 2000.
    for i in range(25):
        bars.append(_bar(base + i * 3600, 2000.0))
    # Sharp dip: drives the typical price well below the SMA → CCI <= -100.
    bars.append(_bar(base + 25 * 3600, 1960.0))
    bars.append(_bar(base + 26 * 3600, 1955.0))
    # Recovery bar: typical price rebounds → CCI crosses back UP through -100.
    bars.append(_bar(base + 27 * 3600, 1990.0))
    return bars


def test_fires_on_cross_up_through_oversold() -> None:
    bars = _oversold_then_revert("GOLD")
    # Sanity: prev CCI at/below -100, current CCI above -100 (the cross).
    typicals = [_typical(b.high, b.low, b.close) for b in bars]
    assert _cci(typicals[:-1]) is not None and _cci(typicals[:-1]) <= -100.0
    assert _cci(typicals) is not None and _cci(typicals) > -100.0
    sig = CCIReversionStrategy().generate_raw_signal(_mv("GOLD", bars))
    assert sig is not None
    assert sig.side == "long"
    assert 0.0 < sig.strength <= 1.0
    assert sig.strategy_id == "cci_reversion"


def test_no_fire_while_still_deeply_oversold() -> None:
    # Drop the recovery bar — still pinned below -100, no cross-up yet.
    bars = _oversold_then_revert("GOLD")[:-1]
    assert CCIReversionStrategy().generate_raw_signal(_mv("GOLD", bars)) is None


def test_no_fire_on_flat_noise() -> None:
    # Pure flat tape: no extreme deviation, never crosses -100.
    base = 1_700_000_000
    bars = [_bar(base + i * 3600, 2000.0) for i in range(28)]
    assert CCIReversionStrategy().generate_raw_signal(_mv("GOLD", bars)) is None


def test_no_fire_on_oversold_without_reversion() -> None:
    # Monotonic decline: CCI stays extreme-negative, never crosses back up.
    base = 1_700_000_000
    bars = [_bar(base + i * 3600, 2000.0) for i in range(25)]
    for j, px in enumerate((1980.0, 1960.0, 1940.0)):
        bars.append(_bar(base + (25 + j) * 3600, px))
    assert CCIReversionStrategy().generate_raw_signal(_mv("GOLD", bars)) is None


def test_rejects_non_commodity_symbol() -> None:
    # An OKX spot / FX symbol is not in the commodity/index universe.
    bars = _oversold_then_revert("BTCUSDT")
    assert CCIReversionStrategy().generate_raw_signal(_mv("BTCUSDT", bars)) is None


def test_warmup_guard() -> None:
    base = 1_700_000_000
    bars = [_bar(base + i * 3600, 2000.0) for i in range(CCI_WINDOW)]  # < warmup
    assert CCIReversionStrategy().generate_raw_signal(_mv("GOLD", bars)) is None


def test_metadata_harvest_present() -> None:
    m = CCIReversionStrategy.metadata
    assert m.strategy_id == "cci_reversion"
    assert m.asset_class == "commodity"
    assert m.venue == "capital"
    assert m.timeframe == "1H"
    assert m.profit_target_r == 1.0


def test_registered_in_registry() -> None:
    from polaris.strategies import STRATEGY_REGISTRY
    assert STRATEGY_REGISTRY.get("cci_reversion") is CCIReversionStrategy


def test_harvest_wiring_reads_target() -> None:
    from polaris.scripts._production_recalc_exit import _profit_target_for_strategy
    assert _profit_target_for_strategy("cci_reversion") == 1.0


def test_cci_formula_matches_definition() -> None:
    # Hand-checked: 20 typicals, last one offset. CCI = (tp - SMA)/(0.015*MAD).
    typicals = [100.0] * 19 + [130.0]
    sma = (100.0 * 19 + 130.0) / 20.0  # 101.5
    mad = (abs(100.0 - sma) * 19 + abs(130.0 - sma)) / 20.0
    expected = (130.0 - sma) / (0.015 * mad)
    got = _cci(typicals)
    assert got is not None
    assert abs(got - expected) < 1e-9
