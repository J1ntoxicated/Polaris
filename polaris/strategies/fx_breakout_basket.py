"""FX Breakout Basket — Capital CFD, basket trend (correlation_group=cfd_fx_trend).

Spec source: vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md (#5).

Symbols: ``EURUSD / GBPUSD / AUDUSD / USDJPY / USDCAD``.
Trigger: ``close > donchian_high_40`` AND ``adx_14 > 20`` (per-symbol; basket
selection is orchestrator-side).
Leverage: 30× (composer applies in Layer 3 sizing).

P0 params:
  - ``window = 40``
  - ``adx_period = 14``
  - ``adx_threshold = 20``
  - ``basket = 5``
"""

from __future__ import annotations

from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)

WINDOW = 40
ADX_PERIOD = 14
ADX_THRESHOLD = 20.0
BASKET_SYMBOLS: frozenset[str] = frozenset(
    {"EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCAD"}
)

# Strength curve + venue constraints (frozen v1).
STRENGTH_BASE = 0.5
ADX_STRENGTH_DENOM = 40.0
TTL_BARS = 6
LEVERAGE_MAX = 30.0


class FXBreakoutBasketStrategy(BaseStrategy):
    metadata = StrategyMetadata(
        strategy_id="fx_breakout_basket",
        timeframe="1H",
        warmup_bars=WINDOW + 5,
        max_positions=5,
        gross_cap=0.36,
        per_symbol_cap=0.12,
        expected_holding_bars=24,
        asset_class="fx",
        venue="capital",
        correlation_group_id="cfd_fx_trend",
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        if market_view.symbol.upper().replace("/", "") not in BASKET_SYMBOLS:
            return None
        bars = market_view.bars
        if len(bars) < WINDOW + 1:
            return None
        last = bars[-1]
        if is_finite(market_view.donchian_high_40):
            high = market_view.donchian_high_40
        else:
            high = max(b.high for b in bars[-(WINDOW + 1):-1])
        if high is None or last.close <= high:
            return None
        if not is_finite(market_view.adx_14):
            return None
        adx = market_view.adx_14
        if adx is None or adx <= ADX_THRESHOLD:
            return None
        adx_score = STRENGTH_BASE + (adx - ADX_THRESHOLD) / ADX_STRENGTH_DENOM
        strength = min(1.0, max(STRENGTH_BASE, adx_score))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=TTL_BARS,
            thesis_tag=f"fx_donchian_40+adx={adx:.1f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={"adx_14": f"{adx:.1f}", "donchian_high_40": f"{high:.5f}",
                  "leverage": f"{int(LEVERAGE_MAX)}"},
        )


__all__ = [
    "ADX_PERIOD",
    "ADX_STRENGTH_DENOM",
    "ADX_THRESHOLD",
    "BASKET_SYMBOLS",
    "FXBreakoutBasketStrategy",
    "LEVERAGE_MAX",
    "STRENGTH_BASE",
    "TTL_BARS",
    "WINDOW",
]
