"""Equity Gap-and-Go — Alpaca US equity, 1D (Track C, T12, NEW).

Gap-up continuation, long-only (P0). Fires when the session opens with a
material upward gap that also clears the prior session's high — the classic
gap-and-go setup where the open imbalance carries the trend.

Trigger:
  - ``open >= prev_close * (1 + GAP_PCT)``  (material upward gap; GAP_PCT=0.02)
  - AND ``open > prev_high``                (gap clears the prior session high)

``prev_close`` / ``gap_pct`` are supplied by ``build_real_market_view`` for the
equity stream; ``prev_high`` is read from ``bars[-2].high``. When ``prev_close``
is unset (defaults None — every non-equity MarketView), the strategy returns
None and never fires, so the 7 existing strategies are unaffected.

P0 params:
  - ``GAP_PCT = 0.02``  (2% minimum gap-up)
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

GAP_PCT = 0.02
WARMUP_BARS = 2

# Strength curve (frozen v1): bigger gap → stronger, floored/capped.
STRENGTH_FLOOR = 0.4
STRENGTH_BASE = 0.5
GAP_GAIN = 5.0
TTL_BARS = 4


class EquityGapGoStrategy(BaseStrategy):
    metadata = StrategyMetadata(
        strategy_id="equity_gap_go",
        timeframe="1D",
        warmup_bars=WARMUP_BARS,
        max_positions=4,
        gross_cap=0.18,
        per_symbol_cap=0.06,
        expected_holding_bars=4,
        asset_class="equity",
        venue="alpaca",
        correlation_group_id="equity_gap",
        product_class="equity",
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < 2:
            return None
        # prev_close / gap_pct are equity-only fields (default None elsewhere).
        if not is_finite(market_view.prev_close):
            return None
        if not is_finite(market_view.gap_pct):
            return None
        prev_close = market_view.prev_close
        gap = market_view.gap_pct
        if prev_close is None or gap is None or prev_close <= 0.0:
            return None
        last = bars[-1]
        prev_high = bars[-2].high
        # Gap-up continuation: material gap AND open clears the prior high.
        if last.open < prev_close * (1.0 + GAP_PCT):
            return None
        if last.open <= prev_high:
            return None
        # Strength scales with the realized gap size, floored/capped.
        scored = STRENGTH_BASE + GAP_GAIN * gap
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=TTL_BARS,
            thesis_tag=f"equity_gap_up={gap:.4f}>prev_high",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={"gap_pct": f"{gap:.4f}", "prev_high": f"{prev_high:.4f}"},
        )


__all__ = [
    "EquityGapGoStrategy",
    "GAP_GAIN",
    "GAP_PCT",
    "STRENGTH_BASE",
    "STRENGTH_FLOOR",
    "TTL_BARS",
    "WARMUP_BARS",
]
