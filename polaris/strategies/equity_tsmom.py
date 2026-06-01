"""Equity TSMOM — Alpaca US equity, 1D rebalance (Track C, T12).

Re-skin of :class:`~polaris.strategies.tsmom.TSMOMStrategy` for the additive
US-equity stream (StreamConfig C_alpaca_equity). Same cross-sectional momentum
logic (``momentum_20bar > 0``), long-only (P0). Only the venue / asset_class /
timeframe / correlation_group differ — the OKX TSMOM strategy is untouched.

Trigger: ``momentum_20bar > 0`` (per-symbol; basket / top_n ranking is the
orchestrator's job — the strategy emits one signal per qualifying symbol).

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

# Strength curve constants (frozen v1 — mirrors OKX TSMOM).
STRENGTH_FLOOR = 0.4
STRENGTH_BASE = 0.5
MOMENTUM_GAIN = 5.0
TTL_BARS = 4


class EquityTSMOMStrategy(BaseStrategy):
    # momentum_gain (signal-STRENGTH/sizing knob) + ttl_bars (inert-in-replay)
    # are intentionally NOT in PARAM_BOUNDS — no entry-set knob (bare
    # momentum trigger). Kept as class defaults for behavior-0.
    momentum_gain: float = MOMENTUM_GAIN
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="equity_tsmom",
        timeframe="1D",
        warmup_bars=LOOKBACK_BARS + 5,
        max_positions=5,
        gross_cap=0.32,
        per_symbol_cap=0.08,
        expected_holding_bars=24,
        asset_class="equity",
        venue="alpaca",
        correlation_group_id="equity_cross_sectional_momo",
        product_class="equity",
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
        scored = STRENGTH_BASE + self.momentum_gain * momentum
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=min(1.0, max(STRENGTH_FLOOR, scored)),
            ttl_bars=self.ttl_bars,
            thesis_tag=f"equity_tsmom_20bar={momentum:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=bars[-1].ts,
            tags={"momentum_20": f"{momentum:.4f}"},
        )


__all__ = [
    "EquityTSMOMStrategy",
    "LOOKBACK_BARS",
    "MOMENTUM_GAIN",
    "STRENGTH_BASE",
    "STRENGTH_FLOOR",
    "TOP_N",
    "TTL_BARS",
]
