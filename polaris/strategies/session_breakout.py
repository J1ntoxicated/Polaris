"""Session Breakout — Capital CFD (correlation_group=cfd_session_event).

Spec source: vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md (#7).

Symbols: ``US500 / US100 / EURUSD / GBPUSD``.
Trigger: inside the first ``30`` minutes after session open, ``close >
session_open_price + atr_period(14) × 1.5``.
Leverage: 20×.

P0 params:
  - ``open_window_minutes = 30``
  - ``atr_period = 14``  (consumed via ``market_view.session_atr``)
  - ``atr_mult = 1.5``
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

OPEN_WINDOW_MINUTES = 30
ATR_PERIOD = 14
ATR_MULT = 1.5
SUPPORTED_SYMBOLS: frozenset[str] = frozenset(
    {"US500", "US100", "EURUSD", "GBPUSD"}
)

# Strength curve + venue constraints (frozen v1).
STRENGTH_BASE = 0.5
EXCESS_GAIN = 0.3
TTL_BARS = 3
LEVERAGE_MAX = 20.0


class SessionBreakoutStrategy(BaseStrategy):
    metadata = StrategyMetadata(
        strategy_id="session_breakout",
        timeframe="5m",
        warmup_bars=ATR_PERIOD + 5,
        max_positions=2,
        gross_cap=0.20,
        per_symbol_cap=0.10,
        expected_holding_bars=12,
        asset_class="index",
        venue="capital",
        correlation_group_id="cfd_session_event",
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        sym = market_view.symbol.upper().replace("/", "").replace(".", "")
        if sym not in SUPPORTED_SYMBOLS:
            return None
        if not market_view.is_session_open_window:
            return None
        if not (
            is_finite(market_view.session_open_price)
            and is_finite(market_view.session_atr)
        ):
            return None
        open_price = market_view.session_open_price
        atr_val = market_view.session_atr
        if open_price is None or atr_val is None or atr_val <= 0.0:
            return None
        threshold = open_price + ATR_MULT * atr_val
        bars = market_view.bars
        if not bars:
            return None
        last = bars[-1]
        if last.close <= threshold:
            return None
        excess = (last.close - threshold) / max(1e-9, atr_val)
        scored = STRENGTH_BASE + EXCESS_GAIN * excess
        strength = min(1.0, max(STRENGTH_BASE, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=TTL_BARS,
            thesis_tag=f"session_open+ATR×{ATR_MULT}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={
                "session_open": f"{open_price:.4f}",
                "session_atr": f"{atr_val:.4f}",
                "leverage": f"{int(LEVERAGE_MAX)}",
            },
        )


__all__ = [
    "ATR_MULT",
    "ATR_PERIOD",
    "EXCESS_GAIN",
    "LEVERAGE_MAX",
    "OPEN_WINDOW_MINUTES",
    "STRENGTH_BASE",
    "SUPPORTED_SYMBOLS",
    "SessionBreakoutStrategy",
    "TTL_BARS",
]
