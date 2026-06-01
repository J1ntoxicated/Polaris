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


def _normalize_basket_symbol(symbol: str) -> str:
    """Reduce a Capital venue epic to the bare FX pair for basket matching.

    Capital's real epics carry a suffix (e.g. AUD/USD lists as ``AUDUSD_ZERO``,
    weekend variants as ``EURUSD_W``), so a plain ``.upper().replace("/", "")``
    misses the major. Strip a trailing ``_SUFFIX`` so ``AUDUSD_ZERO`` /
    ``EURUSD_W`` / ``EUR/USD`` all reduce to the bare pair in ``BASKET_SYMBOLS``.
    Mirrors ``core.universe.schema._normalize_fx_symbol`` (same SSOT intent).
    """
    s = symbol.upper().replace("/", "")
    if "_" in s:
        s = s.split("_", 1)[0]
    return s

# Strength curve + venue constraints (frozen v1).
STRENGTH_BASE = 0.5
ADX_STRENGTH_DENOM = 40.0
TTL_BARS = 6
LEVERAGE_MAX = 30.0


class FXBreakoutBasketStrategy(BaseStrategy):
    # Varyable ENTRY-trigger knob (P0a). Class defaults == module constants ==
    # frozen baseline -> behavior-0 for default instances.
    adx_threshold: float = ADX_THRESHOLD
    # ttl_bars intentionally NOT in PARAM_BOUNDS (inert-in-replay). Behavior-0.
    ttl_bars: int = TTL_BARS

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
        if _normalize_basket_symbol(market_view.symbol) not in BASKET_SYMBOLS:
            return None
        bars = market_view.bars
        if len(bars) < WINDOW + 1:
            return None
        last = bars[-1]
        # adx is a direction-agnostic gate (trend strength, not direction).
        if not is_finite(market_view.adx_14):
            return None
        adx = market_view.adx_14
        if adx is None or adx <= self.adx_threshold:
            return None
        adx_score = STRENGTH_BASE + (adx - self.adx_threshold) / ADX_STRENGTH_DENOM
        strength = min(1.0, max(STRENGTH_BASE, adx_score))
        if is_finite(market_view.donchian_high_40):
            high = market_view.donchian_high_40
        else:
            high = max(b.high for b in bars[-(WINDOW + 1):-1])
        # Long branch first (mutually exclusive with short).
        if high is not None and last.close > high:
            return RawSignal(
                signal_id=make_signal_id(),
                strategy_id=self.metadata.strategy_id,
                symbol=market_view.symbol,
                side="long",
                strength=strength,
                sizing_hint=strength,
                ttl_bars=self.ttl_bars,
                thesis_tag=f"fx_donchian_40+adx={adx:.1f}",
                correlation_group=self.metadata.correlation_group_id,
                venue_constraints={"leverage_max": LEVERAGE_MAX},
                created_at_bar=last.ts,
                tags={"adx_14": f"{adx:.1f}", "donchian_high_40": f"{high:.5f}",
                      "leverage": f"{int(LEVERAGE_MAX)}"},
            )
        if is_finite(market_view.donchian_low_40):
            low = market_view.donchian_low_40
        else:
            low = min(b.low for b in bars[-(WINDOW + 1):-1])
        # Symmetric short branch: break below the 40-bar low (same adx gate).
        if low is not None and last.close < low:
            return RawSignal(
                signal_id=make_signal_id(),
                strategy_id=self.metadata.strategy_id,
                symbol=market_view.symbol,
                side="short",
                strength=strength,
                sizing_hint=strength,
                ttl_bars=self.ttl_bars,
                thesis_tag=f"fx_donchian_40_short+adx={adx:.1f}",
                correlation_group=self.metadata.correlation_group_id,
                venue_constraints={"leverage_max": LEVERAGE_MAX},
                created_at_bar=last.ts,
                tags={"adx_14": f"{adx:.1f}", "donchian_low_40": f"{low:.5f}",
                      "leverage": f"{int(LEVERAGE_MAX)}"},
            )
        return None


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
