"""Spot Donchian — OKX SPOT, 1H (correlation_group=spot_breakout).

Spec source: vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md (#4).

Trigger: ``close > donchian_high_40`` AND ``adx_14 > 20``.

P0 params:
  - ``window = 40``
  - ``adx_period = 14``  (consumed via ``market_view.adx_14``)
  - ``adx_threshold = 20``
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
ADX_THRESHOLD = 14.0  # relaxed 20 -> 14 (flow_not_block, more emits): a weaker-trend donchian break now fires

# Strength curve (frozen v1).
STRENGTH_BASE = 0.5
ADX_STRENGTH_DENOM = 40.0
TTL_BARS = 6


class SpotDonchianStrategy(BaseStrategy):
    # Varyable ENTRY-trigger knob (P0a). Class defaults == module constants ==
    # frozen baseline -> behavior-0 for default instances.
    adx_threshold: float = ADX_THRESHOLD
    # ttl_bars intentionally NOT in PARAM_BOUNDS (inert-in-replay). Behavior-0.
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="spot_donchian",
        timeframe="1H",
        warmup_bars=WINDOW + 5,
        max_positions=3,
        gross_cap=0.20,
        per_symbol_cap=0.07,
        expected_holding_bars=24,
        asset_class="spot",
        venue="okx",
        correlation_group_id="spot_breakout",
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
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
        if adx is None or adx <= self.adx_threshold:
            return None
        adx_score = STRENGTH_BASE + (adx - self.adx_threshold) / ADX_STRENGTH_DENOM
        strength = min(1.0, max(STRENGTH_BASE, adx_score))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"donchian_40+adx={adx:.1f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={"adx_14": f"{adx:.1f}", "donchian_high_40": f"{high:.4f}"},
        )


__all__ = [
    "ADX_PERIOD",
    "ADX_STRENGTH_DENOM",
    "ADX_THRESHOLD",
    "STRENGTH_BASE",
    "SpotDonchianStrategy",
    "TTL_BARS",
    "WINDOW",
]
