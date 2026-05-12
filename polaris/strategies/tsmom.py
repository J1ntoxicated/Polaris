"""TSMOM 20-bar — OKX SPOT, 1H rebalance (correlation_group=spot_cross_sectional_momo).

Spec source: vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md (#2).

Trigger: ``momentum_20bar > 0`` (per-symbol; the basket selection / top_n
ranking is the orchestrator's responsibility — the strategy emits one signal
per qualifying symbol).

P0 params:
  - ``lookback_bars = 20``
  - ``top_n = 5``  (orchestrator side)
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

LOOKBACK_BARS = 20
TOP_N = 5

# Strength curve constants (frozen v1).
STRENGTH_FLOOR = 0.4
STRENGTH_BASE = 0.5
MOMENTUM_GAIN = 5.0
TTL_BARS = 4


class TSMOMStrategy(BaseStrategy):
    metadata = StrategyMetadata(
        strategy_id="tsmom",
        timeframe="1H",
        warmup_bars=LOOKBACK_BARS + 5,
        max_positions=5,
        gross_cap=0.32,
        per_symbol_cap=0.08,
        expected_holding_bars=24,
        asset_class="spot",
        venue="okx",
        correlation_group_id="spot_cross_sectional_momo",
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < LOOKBACK_BARS + 1:
            return None
        # Compute momentum if not pre-fed by orchestrator.
        if is_finite(market_view.momentum_20bar):
            momentum = market_view.momentum_20bar
        else:
            past_close = bars[-(LOOKBACK_BARS + 1)].close
            if past_close <= 0:
                return None
            momentum = (bars[-1].close - past_close) / past_close
        if momentum is None or momentum <= 0.0:
            return None
        # Strength scales with raw momentum, capped.
        scored = STRENGTH_BASE + MOMENTUM_GAIN * momentum
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=min(1.0, max(STRENGTH_FLOOR, scored)),
            ttl_bars=TTL_BARS,
            thesis_tag=f"tsmom_20bar={momentum:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=bars[-1].ts,
            tags={"momentum_20": f"{momentum:.4f}"},
        )


__all__ = [
    "LOOKBACK_BARS",
    "MOMENTUM_GAIN",
    "STRENGTH_BASE",
    "STRENGTH_FLOOR",
    "TOP_N",
    "TSMOMStrategy",
    "TTL_BARS",
]
