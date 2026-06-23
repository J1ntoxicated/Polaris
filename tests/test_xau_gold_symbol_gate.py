"""Wave 1 — xau_indices_trend accepts the LIVE Capital commodity symbol 'GOLD'.

DEMO/PAPER. The strategy's SUPPORTED_SYMBOLS listed only 'XAUUSD' but the live
Capital universe symbol is 'GOLD' (asset_class=commodity). With routing now open,
the symbol gate must MATCH 'GOLD' or gold stays dead. Purely additive: 'XAUUSD'
is kept (safety), 'GOLD' added.
"""

from __future__ import annotations

from polaris.strategies.base import BarView, MarketView
from polaris.strategies.xau_indices_trend import (
    SUPPORTED_SYMBOLS,
    XAUIndicesTrendStrategy,
)


def test_gold_in_supported_symbols() -> None:
    assert "GOLD" in SUPPORTED_SYMBOLS
    # XAUUSD kept for safety (additive, not a replace).
    assert "XAUUSD" in SUPPORTED_SYMBOLS


def _ramp_view(symbol: str) -> MarketView:
    # 40 rising bars → close breaks the 30-bar donchian high with +momentum, so
    # the ONLY thing that can gate the long is the symbol membership check.
    bars: list[BarView] = []
    price = 100.0
    for i in range(40):
        price += 1.0
        bars.append(
            BarView(ts=1_780_000_000 + i * 3600, open=price - 0.5,
                     high=price + 0.2, low=price - 0.7, close=price, volume=1000.0)
        )
    return MarketView(
        symbol=symbol, venue="capital", timeframe="1H", bars=bars,
        last_price=bars[-1].close, spread_bps=2.0, momentum_20bar=0.15,
    )


def test_gold_symbol_gate_passes_emits_signal() -> None:
    strat = XAUIndicesTrendStrategy()
    sig = strat.generate_raw_signal(_ramp_view("GOLD"))
    assert sig is not None
    assert sig.side == "long"


def test_unsupported_symbol_still_gated() -> None:
    # A symbol the strategy does NOT support is still rejected (no widening).
    strat = XAUIndicesTrendStrategy()
    assert strat.generate_raw_signal(_ramp_view("NATURALGAS")) is None
