"""CCI(20) Oversold Reversion — Capital commodity/index CFD, mean-reversion.

Fills the commodity reversion gap: ``xau_indices_trend`` is a TREND strategy on
the same Capital commodity/index instruments (Donchian + momentum), so a deeply
oversold-but-reverting tape went untraded. CCI (Commodity Channel Index) is the
classic statistically-extreme deviation oscillator for exactly this regime.

CCI(20) = (typical_price - SMA(typical, 20)) / (0.015 * mean_deviation), where
``typical_price = (H + L + C) / 3`` and ``mean_deviation`` is the mean absolute
deviation of the last 20 typical prices from their SMA.

ENTRY long: CCI(20) crosses back UP through -100 (prev bar <= -100, this bar
> -100) — the statistically-extreme deviation is reverting toward the mean.
EXIT (downstream): CCI → 0 (mean) or +100; declared as ``profit_target_r = 1.0``
so the precise-exit engine HARVESTS the bounded revert instead of letting the
wide ATR trail round-trip it back to ~0R.

Symbols: the Capital commodity/index majors, shared with ``xau_indices_trend``
(``SUPPORTED_SYMBOLS``) — gold + the index CFDs. LONG-ONLY (the spec is an
oversold-reversion long); short reversion is left to other strategies.
"""

from __future__ import annotations

from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    make_signal_id,
)
from polaris.strategies.xau_indices_trend import SUPPORTED_SYMBOLS

CCI_WINDOW = 20
CCI_CONSTANT = 0.015
CCI_OVERSOLD = -70.0  # relaxed -100 -> -70 (flow_not_block, more emits): a shallower oversold cross-up now fires
# extreme→mean = -70 CCI revert; 1 R harvested at the bounded target. Same
# EXPECTANCY rationale as fx_range_fade.FADE_TARGET_R — a per-position close
# target so the revert is banked, NOT a size dampen / entry block (flow_not_block).
REVERSION_TARGET_R = 1.0
STRENGTH_BASE = 0.5
DEPTH_GAIN = 0.2  # deeper prior oversold (more negative CCI) → stronger signal
TTL_BARS = 4
LEVERAGE_MAX = 20.0


def _typical(high: float, low: float, close: float) -> float:
    return (high + low + close) / 3.0


def _cci(typicals: list[float]) -> float | None:
    """CCI over the last ``CCI_WINDOW`` typical prices (last element = current)."""
    if len(typicals) < CCI_WINDOW:
        return None
    window = typicals[-CCI_WINDOW:]
    sma = sum(window) / CCI_WINDOW
    mean_dev = sum(abs(tp - sma) for tp in window) / CCI_WINDOW
    if mean_dev <= 0.0:
        return None  # flat window — no deviation to normalise (avoid /0)
    return (window[-1] - sma) / (CCI_CONSTANT * mean_dev)


class CCIReversionStrategy(BaseStrategy):
    # depth_gain / ttl_bars are signal-STRENGTH + lifecycle knobs (not entry-set
    # triggers) → kept as class defaults for behavior-0, not in PARAM_BOUNDS.
    depth_gain: float = DEPTH_GAIN
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="cci_reversion",
        timeframe="1H",
        warmup_bars=CCI_WINDOW + 5,
        max_positions=4,
        gross_cap=0.36,
        per_symbol_cap=0.12,
        expected_holding_bars=8,
        asset_class="commodity",
        venue="capital",
        correlation_group_id="cfd_commodity_reversion",
        profit_target_r=REVERSION_TARGET_R,
        # UNVALIDATED — gap-filling design rationale only (no OOS / fee evidence).
        # Stays OUT of NEW-ENTRY dispatch (it was already absent from the prior
        # literal); this flag makes the KILL explicit + structurally enforced
        # (registered, not dispatched).
        dispatch_eligible=False,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        sym = market_view.symbol.upper().replace("/", "").replace(".", "")
        if sym not in SUPPORTED_SYMBOLS:
            return None
        bars = market_view.bars
        # Need the current CCI window AND one prior bar to detect the cross-up.
        if len(bars) < CCI_WINDOW + 1:
            return None
        typicals = [_typical(b.high, b.low, b.close) for b in bars]
        cci_now = _cci(typicals)
        cci_prev = _cci(typicals[:-1])
        if cci_now is None or cci_prev is None:
            return None
        # ENTRY: CCI crosses back UP through -100 (was at/below extreme, now above).
        if not (cci_prev <= CCI_OVERSOLD and cci_now > CCI_OVERSOLD):
            return None
        last = bars[-1]
        # Strength: deeper prior oversold (more negative cci_prev) → stronger revert.
        depth = (CCI_OVERSOLD - cci_prev) / 100.0  # 0 at -100, grows as prev deepens
        strength = min(1.0, max(STRENGTH_BASE, STRENGTH_BASE + self.depth_gain * depth))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"cci20_cross_up_oversold+cci={cci_now:.1f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={
                "cci_now": f"{cci_now:.1f}",
                "cci_prev": f"{cci_prev:.1f}",
                "side": "long",
                "leverage": f"{int(LEVERAGE_MAX)}",
            },
        )


__all__ = [
    "CCIReversionStrategy",
    "CCI_OVERSOLD",
    "CCI_WINDOW",
    "REVERSION_TARGET_R",
    "STRENGTH_BASE",
    "TTL_BARS",
]
