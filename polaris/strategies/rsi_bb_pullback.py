"""RSI-BB Pullback — OKX SPOT, 15m bar (correlation_group=spot_mean_reversion).

Spec source: vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md (#3).

Trigger: ``rsi_14 < 30`` AND ``last_low <= bb_lower`` AND ``close > ma_200``
(trend filter — only buy dips inside an uptrend).

P0 params:
  - ``rsi_period = 14``  (consumed via ``market_view.rsi_14``)
  - ``rsi_threshold = 30``
  - ``bb_window = 20``
  - ``bb_std = 2``
  - ``trend_filter_ma = 200``
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

RSI_PERIOD = 14
RSI_THRESHOLD = 30.0
BB_WINDOW = 20
BB_STD = 2.0
TREND_FILTER_MA = 200

# Strength curve (frozen v1).
STRENGTH_FLOOR = 0.4
STRENGTH_OFFSET = 0.5
TTL_BARS = 4


class RSIBBPullbackStrategy(BaseStrategy):
    metadata = StrategyMetadata(
        strategy_id="rsi_bb_pullback",
        timeframe="15m",
        warmup_bars=TREND_FILTER_MA + 5,
        max_positions=4,
        gross_cap=0.18,
        per_symbol_cap=0.06,
        expected_holding_bars=8,
        asset_class="spot",
        venue="okx",
        correlation_group_id="spot_mean_reversion",
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        if not is_finite(market_view.rsi_14):
            return None
        if not is_finite(market_view.bb_lower):
            return None
        if not is_finite(market_view.ma_200):
            return None
        rsi = market_view.rsi_14
        bb_lo = market_view.bb_lower
        ma200 = market_view.ma_200
        bars = market_view.bars
        if not bars:
            return None
        last = bars[-1]
        if rsi is None or rsi >= RSI_THRESHOLD:
            return None
        if bb_lo is None or last.low > bb_lo:
            return None
        if ma200 is None or last.close <= ma200:
            return None
        # Strength: deeper RSI < 30 → stronger.
        depth = (RSI_THRESHOLD - rsi) / RSI_THRESHOLD
        strength = min(1.0, max(STRENGTH_FLOOR, depth + STRENGTH_OFFSET))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=TTL_BARS,
            thesis_tag=f"rsi={rsi:.1f}<30+bb_lo+ma200",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={"rsi": f"{rsi:.1f}", "ma_200": f"{ma200:.2f}"},
        )


__all__ = [
    "BB_STD",
    "BB_WINDOW",
    "RSI_PERIOD",
    "RSI_THRESHOLD",
    "RSIBBPullbackStrategy",
    "STRENGTH_FLOOR",
    "STRENGTH_OFFSET",
    "TREND_FILTER_MA",
    "TTL_BARS",
]
