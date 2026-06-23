"""XAU/Indices Trend — Capital CFD (correlation_group=cfd_index_commodity_trend).

Spec source: vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md (#6).

Symbols: ``XAUUSD / GOLD / US500 / US100 / GER40``.
Trigger: ``close > donchian_high_30`` AND ``momentum_20bar > 0`` (20d momentum).
Leverage: 20×.

P0 params:
  - ``donchian = 30``
  - ``momentum_lookback = 20``
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

DONCHIAN_WINDOW = 30
MOMENTUM_LOOKBACK = 20
# 'GOLD' is the LIVE Capital commodity symbol (asset_class=commodity); 'XAUUSD'
# is kept for safety (additive, not a replace) so any path still carrying the
# legacy spelling matches too. Without 'GOLD' the symbol gate below rejected the
# live universe symbol → gold's whole universe was a structural 0%-emit dead end.
SUPPORTED_SYMBOLS: frozenset[str] = frozenset(
    {"XAUUSD", "GOLD", "US500", "US100", "DE40", "UK100", "EU50", "US30"}
)

# Strength curve + venue constraints (frozen v1).
STRENGTH_BASE = 0.5
MOMENTUM_GAIN = 4.0
TTL_BARS = 6
LEVERAGE_MAX = 20.0


class XAUIndicesTrendStrategy(BaseStrategy):
    # momentum_gain (signal-STRENGTH/sizing knob) + ttl_bars (inert-in-replay)
    # are intentionally NOT in PARAM_BOUNDS — no entry-set knob (bare
    # momentum trigger). Kept as class defaults for behavior-0.
    momentum_gain: float = MOMENTUM_GAIN
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="xau_indices_trend",
        timeframe="1H",
        warmup_bars=DONCHIAN_WINDOW + 5,
        max_positions=4,
        gross_cap=0.40,
        per_symbol_cap=0.16,
        expected_holding_bars=48,
        asset_class="commodity",
        venue="capital",
        correlation_group_id="cfd_index_commodity_trend",
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        sym = market_view.symbol.upper().replace("/", "").replace(".", "")
        if sym not in SUPPORTED_SYMBOLS:
            return None
        bars = market_view.bars
        if len(bars) < max(DONCHIAN_WINDOW, MOMENTUM_LOOKBACK) + 1:
            return None
        last = bars[-1]
        if is_finite(market_view.momentum_20bar):
            momentum = market_view.momentum_20bar
        else:
            past_close = bars[-(MOMENTUM_LOOKBACK + 1)].close
            if past_close <= 0:
                return None
            momentum = (last.close - past_close) / past_close
        if momentum is None:
            return None
        if is_finite(market_view.donchian_high_30):
            high = market_view.donchian_high_30
        else:
            high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
        # Long branch first (mutually exclusive with short).
        if high is not None and last.close > high and momentum > 0.0:
            scored = STRENGTH_BASE + self.momentum_gain * momentum
            strength = min(1.0, max(STRENGTH_BASE, scored))
            return RawSignal(
                signal_id=make_signal_id(),
                strategy_id=self.metadata.strategy_id,
                symbol=market_view.symbol,
                side="long",
                strength=strength,
                sizing_hint=strength,
                ttl_bars=self.ttl_bars,
                thesis_tag=f"donchian_30+mom_20={momentum:.4f}",
                correlation_group=self.metadata.correlation_group_id,
                venue_constraints={"leverage_max": LEVERAGE_MAX},
                created_at_bar=last.ts,
                tags={"momentum_20": f"{momentum:.4f}",
                      "donchian_high_30": f"{high:.4f}",
                      "leverage": f"{int(LEVERAGE_MAX)}"},
            )
        if is_finite(market_view.donchian_low_30):
            low = market_view.donchian_low_30
        else:
            low = min(b.low for b in bars[-(DONCHIAN_WINDOW + 1):-1])
        # Symmetric short branch: break below 30-bar low with negative momentum.
        if low is not None and last.close < low and momentum < 0.0:
            scored = STRENGTH_BASE + self.momentum_gain * (-momentum)
            strength = min(1.0, max(STRENGTH_BASE, scored))
            return RawSignal(
                signal_id=make_signal_id(),
                strategy_id=self.metadata.strategy_id,
                symbol=market_view.symbol,
                side="short",
                strength=strength,
                sizing_hint=strength,
                ttl_bars=self.ttl_bars,
                thesis_tag=f"donchian_30_short+mom_20={momentum:.4f}",
                correlation_group=self.metadata.correlation_group_id,
                venue_constraints={"leverage_max": LEVERAGE_MAX},
                created_at_bar=last.ts,
                tags={"momentum_20": f"{momentum:.4f}",
                      "donchian_low_30": f"{low:.4f}",
                      "leverage": f"{int(LEVERAGE_MAX)}"},
            )
        return None


__all__ = [
    "DONCHIAN_WINDOW",
    "LEVERAGE_MAX",
    "MOMENTUM_GAIN",
    "MOMENTUM_LOOKBACK",
    "STRENGTH_BASE",
    "SUPPORTED_SYMBOLS",
    "TTL_BARS",
    "XAUIndicesTrendStrategy",
]
