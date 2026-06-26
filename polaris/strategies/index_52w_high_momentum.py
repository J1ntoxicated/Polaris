"""index_52w_high_momentum — Capital CFD index, 52-week-high momentum (let-run).

Spec source: research selection ``index_52w_high_momentum`` (rank-3, DEMO/PAPER,
NET +58bps verified, both OOS halves + (+97/+20bps), 722-trade backtest). The
George/Hwang anchoring-continuation edge: buy a FRESH 252-bar (52-week) high within
2% of the high WITH a 3-month momentum confirm, and let the new-high leg run.
Adds the Asia-Pacific index complex (J225/HK50/AU200) that currently has ZERO
strategy coverage. Distinct from ``xau_indices_trend`` (30-bar Donchian) and from
``index_dual_momentum_rotation`` (monthly cross-sectional rotation) — orthogonal:
this is an event-driven per-symbol breakout on each index's own 252-bar history.

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
The verified asymmetric let-winners-run EXIT (initial hard stop entry-2.0*ATR(20) +
a 3.0*ATR(20) chandelier let-run trail re-armed only after +0.5R MFE — the wide
trail lets the new-high leg run, do NOT harvest at +1R) is owned by the G5/G7 gates
via ``StrategyMetadata`` (TREND bucket — ``correlation_group_id`` has no reversion
substring → let-winners-run; ``hold_overnight=True``; ``profit_target_r=None`` so
winners run unbounded; ``expected_holding_bars=20``). Caveat: edge DECAYS across
time (2nd OOS half +20bps vs 1st +97bps, never flips negative) — monitor for
further decay. flow_not_block.

ENTRY (1d bar close, LONG-only, per-symbol on its own history): emit when
  * ``close >= 0.98 * max(high[prior 252 bars])`` (within 2% of the 52-week high)
  * AND ``close > max(high[prior 252 bars])`` (a FRESH 252-bar high THIS bar —
    the SAME prior-252 window, EXCLUDING the current closing bar = ``bars[-253:-1]``)
  * AND ``ROC_60 = close/close[-61] - 1 > 0`` (3-month momentum confirm).
The 252-bar window EXCLUDES the current closing bar (no look-ahead). The 252-bar
high + ROC_60 are NOT pre-fed (only Donchian 40/30 + momentum_20bar are) — both are
recomputed in-module from ``market_view.bars`` (is_finite fallback for the high).
One emit per fresh 52w-high event — the anti-churn 5-bar cooldown is enforced
downstream by the dispatcher (``bar_seconds("1D")`` cooldown +
``concurrent_same_side_open``), NOT re-implemented in-module.

Verified params are named Final constants (no magic numbers):
  - ``HIGH_LOOKBACK = 252`` / ``PROXIMITY = 0.98`` / ``ROC_LOOKBACK = 60``
  - ``ATR_STOP_MULT = 2.0`` / ``ATR_TRAIL_MULT = 3.0`` (G7-consumed exit basis)
"""

from __future__ import annotations

from typing import Final

from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    make_signal_id,
)

HIGH_LOOKBACK: Final[int] = 252
PROXIMITY: Final[float] = 0.98
ROC_LOOKBACK: Final[int] = 60
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
ATR_STOP_MULT: Final[float] = 2.0
ATR_TRAIL_MULT: Final[float] = 3.0

# Strength curve (frozen v1). Scales with the ROC_60 momentum gain, floored +
# capped. EXPECTANCY-positive size, never a dampen.
STRENGTH_FLOOR: Final[float] = 0.5
ROC_STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3
LEVERAGE_MAX: Final[float] = 20.0

# Live Capital bare index epics (AU200AU alias accepted). Yahoo ^GSPC/^NDX/^N225/
# ^HSI/^AXJO are internal fetch details only — NEVER the RawSignal.symbol.
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset(
    {"US500", "US100", "J225", "HK50", "AU200", "AU200AU"}
)


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace(".", "")


class Index52WHighMomentumStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="index_52w_high_momentum",
        timeframe="1D",
        warmup_bars=HIGH_LOOKBACK + 1,  # 253
        max_positions=4,
        gross_cap=0.30,
        per_symbol_cap=0.10,
        expected_holding_bars=20,
        asset_class="index",
        venue="capital",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="cfd_index_52w_high_momentum",
        product_class="cfd",
        hold_overnight=True,
        profit_target_r=None,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if _norm_symbol(market_view.symbol) not in SUPPORTED_SYMBOLS:
            return None
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < HIGH_LOOKBACK + 1:
            return None
        last = bars[-1]

        # 252-bar prior high: EXCLUDE the current closing bar (no look-ahead).
        # NOT pre-fed → recompute from bars.
        prior_high_252 = max(b.high for b in bars[-(HIGH_LOOKBACK + 1):-1])
        if prior_high_252 <= 0.0:
            return None
        # (a) within 2% of the 52-week high AND (b) a FRESH 252-bar high this bar.
        if last.close < PROXIMITY * prior_high_252:
            return None
        if last.close <= prior_high_252:
            return None

        # ROC_60 (3-month) momentum confirm. Reads only closed bars (no look-ahead).
        base_close = bars[-(ROC_LOOKBACK + 1)].close
        if base_close <= 0.0:
            return None
        roc_60 = last.close / base_close - 1.0
        if roc_60 <= 0.0:
            return None

        scored = STRENGTH_FLOOR + ROC_STRENGTH_GAIN * roc_60
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"index_52w_high+roc_60={roc_60:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={
                "high_252": f"{prior_high_252:.4f}",
                "roc_60": f"{roc_60:.4f}",
                "leverage": f"{int(LEVERAGE_MAX)}",
            },
        )


__all__ = [
    "ATR_STOP_MULT",
    "ATR_TRAIL_MULT",
    "HIGH_LOOKBACK",
    "Index52WHighMomentumStrategy",
    "LEVERAGE_MAX",
    "PROXIMITY",
    "ROC_LOOKBACK",
    "ROC_STRENGTH_GAIN",
    "STRENGTH_FLOOR",
    "SUPPORTED_SYMBOLS",
    "TTL_BARS",
]
