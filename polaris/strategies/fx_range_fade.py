"""FX Range Fade — Capital CFD, mean-reversion in low-ADX ranges (bidirectional).

FX majors spend most of the session RANGING, not breaking out. The existing
``fx_breakout_basket`` (Donchian + ADX>20) and ``session_breakout`` (session-open
momentum) only fire on trends / session opens, so FX went untraded mid-session
(live: forex evaluated but no-emit). This fades Bollinger-band extremes back to
the middle, but ONLY when ADX is LOW (no trend): a trending tape (high ADX) yields
to the breakout strategies, which keeps the fade from bleeding into a runaway move
— the trend guard the design review demanded. ADX_14 is already computed in the
MarketView (the standard trend-strength classifier), so no new regime infra.

Symbols: EURUSD / GBPUSD / AUDUSD / USDJPY / USDCAD (the active Capital majors,
shared SSOT with ``fx_breakout_basket.BASKET_SYMBOLS``).
Trigger: ``adx_14 < 20`` (range) AND (``close >= bb_upper`` → SHORT |
``close <= bb_lower`` → LONG). Target (downstream exit): ``bb_middle``.
ADX bands are disjoint from fx_breakout_basket (>20 trend) → no same-bar conflict.
"""

from __future__ import annotations

from typing import Literal

from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)
from polaris.strategies.fx_breakout_basket import (
    BASKET_SYMBOLS,
    _normalize_basket_symbol,
)

ADX_RANGE_MAX = 20.0  # fade only BELOW this (>= is a trend → yield to fx_breakout)
STRENGTH_BASE = 0.5
TTL_BARS = 4
LEVERAGE_MAX = 30.0


class FXRangeFadeStrategy(BaseStrategy):
    # Varyable ENTRY-trigger knob; class default == module constant == behavior-0.
    adx_range_max: float = ADX_RANGE_MAX
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="fx_range_fade",
        timeframe="1H",
        warmup_bars=30,
        max_positions=5,
        gross_cap=0.36,
        per_symbol_cap=0.12,
        expected_holding_bars=6,
        asset_class="fx",
        venue="capital",
        correlation_group_id="cfd_fx_range",
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        if _normalize_basket_symbol(market_view.symbol) not in BASKET_SYMBOLS:
            return None
        bars = market_view.bars
        if not bars:
            return None
        # Need the trend gate + all three bands finite.
        if not (
            is_finite(market_view.adx_14)
            and is_finite(market_view.bb_upper)
            and is_finite(market_view.bb_lower)
            and is_finite(market_view.bb_middle)
        ):
            return None
        adx = market_view.adx_14
        # RANGE gate (the trend guard): fade ONLY when there is no trend. A high-ADX
        # trending tape is left to the breakout strategies so a fade never fights a
        # runaway move (the classic mean-reversion blow-up).
        if adx is None or adx >= self.adx_range_max:
            return None
        upper = market_view.bb_upper
        lower = market_view.bb_lower
        if upper is None or lower is None or upper <= lower:
            return None
        last = bars[-1]
        close = last.close
        half = (upper - lower) / 2.0
        # Strength: clearer range (lower ADX) + deeper band overshoot → stronger.
        adx_factor = (self.adx_range_max - adx) / self.adx_range_max  # 0..1
        side: Literal["long", "short"]
        if close >= upper:
            overshoot = (close - upper) / half if half > 0.0 else 0.0
            side = "short"  # fade the up-extreme back toward bb_middle
        elif close <= lower:
            overshoot = (lower - close) / half if half > 0.0 else 0.0
            side = "long"  # fade the down-extreme
        else:
            return None  # price inside the bands — no extreme to fade
        strength = min(
            1.0,
            max(STRENGTH_BASE, STRENGTH_BASE + 0.3 * adx_factor + 0.2 * min(overshoot, 1.0)),
        )
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side=side,
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"fx_range_fade_{side}+adx={adx:.1f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={
                "adx_14": f"{adx:.1f}",
                "bb_mid": f"{market_view.bb_middle:.5f}",
                "side": side,
                "leverage": f"{int(LEVERAGE_MAX)}",
            },
        )


__all__ = ["ADX_RANGE_MAX", "FXRangeFadeStrategy"]
