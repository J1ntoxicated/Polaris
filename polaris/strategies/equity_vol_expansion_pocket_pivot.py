"""equity_vol_expansion_pocket_pivot — Alpaca US equity, pocket-pivot thrust (let-run).

Spec source: research selection ``equity_vol_expansion_pocket_pivot`` (rank-7,
DEMO/PAPER, headline NET +389bps / sharpe 3.84 — RANKED LAST despite the highest
headline because its 2017-2026 backtest universe is bull-only growth-leaders with
NO long bear-market OOS, so the headline is the least trustworthy of the 7; 13/15
symbols positive, top-3 removed still +289bps — not a single moonshot). The
O'Neil/Morales pocket-pivot: a volume-confirmed accumulation thrust inside an
uptrend. Distinct from the KILLed ``volume_burst`` (intraday churn) — daily bar +
let-run.

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
The verified asymmetric let-winners-run EXIT (3.0*ATR(20) chandelier trail off the
running peak OR a structural exit on ``close < SMA(50)`` for 1 bar — NO fixed
profit target on the runner) is owned by the G5/G7 gates via ``StrategyMetadata``
(TREND bucket — ``correlation_group_id`` has no reversion substring →
let-winners-run; ``hold_overnight=True``; ``profit_target_r=None`` so winners run
unbounded; ``expected_holding_bars=20``). The asymmetric let-run signature (win
44%, payoff 2.76) = the OPPOSITE of the KILLed intraday volume_burst's
inverted-asymmetry high-churn. flow_not_block.

ENTRY (1d bar close, LONG-only): emit when ALL of —
  * ``close > SMA(10)`` AND ``close > SMA(50)`` AND ``close > SMA(200)``
    (uptrend + regime gate),
  * the bar closes in the UPPER THIRD of its range,
  * ``volume > 1.25 * SMA(50)-of-volume`` (volume expansion),
  * the up-day's volume EXCEEDS the largest DOWN-day volume of the prior 10 bars
    (the pocket-pivot accumulation signature).
All SMAs + volume stats are recomputed in-module from ``market_view.bars`` (volume
is in ``BarView.volume``; the ``connors_rsi2`` _sma pattern; ma_200 via is_finite
fallback). strength scales with the volume-expansion ratio, floored 0.5 capped 1.0.

INERT until the Alpaca SIP key (#42) routes equity bars (degrade-never-crash).

Verified params are named Final constants (no magic numbers):
  - ``SMA_FAST = 10`` / ``SMA_MID = 50`` / ``SMA_SLOW = 200``
  - ``VOL_MA = 50`` / ``VOL_MULT = 1.25`` / ``DOWN_VOL_LOOKBACK = 10``
  - ``UPPER_RANGE_FRAC = 2/3`` / ``ATR_TRAIL_MULT = 3.0`` (G7-consumed exit basis)
"""

from __future__ import annotations

from typing import Final

from polaris.strategies.base import (
    BarView,
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)

SMA_FAST: Final[int] = 10
SMA_MID: Final[int] = 50
SMA_SLOW: Final[int] = 200
VOL_MA: Final[int] = 50
VOL_MULT: Final[float] = 1.25
DOWN_VOL_LOOKBACK: Final[int] = 10
UPPER_RANGE_FRAC: Final[float] = 2.0 / 3.0
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
ATR_TRAIL_MULT: Final[float] = 3.0

# Strength curve (frozen v1). Scales with the volume-expansion ratio over the
# 1.25× threshold, floored + capped. EXPECTANCY-positive size, never a dampen.
STRENGTH_FLOOR: Final[float] = 0.5
VOL_STRENGTH_GAIN: Final[float] = 0.5
TTL_BARS: Final[int] = 3


def _sma(bars: list[BarView], window: int) -> float | None:
    """Simple moving average of the last ``window`` closes (None if too few)."""
    if window <= 0 or len(bars) < window:
        return None
    total = 0.0
    for bar in bars[-window:]:
        total += bar.close
    return total / window


def _vol_sma(bars: list[BarView], window: int) -> float | None:
    """Simple moving average of the last ``window`` volumes (None if too few)."""
    if window <= 0 or len(bars) < window:
        return None
    total = 0.0
    for bar in bars[-window:]:
        total += bar.volume
    return total / window


def _max_down_volume(bars: list[BarView], lookback: int) -> float:
    """Largest volume among the DOWN bars (close < open) of the prior ``lookback``
    bars EXCLUDING the current bar (no look-ahead). 0.0 if there were no down bars.
    """
    window = bars[-(lookback + 1):-1]
    down_vols = [b.volume for b in window if b.close < b.open]
    return max(down_vols) if down_vols else 0.0


class EquityVolExpansionPocketPivotStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="equity_vol_expansion_pocket_pivot",
        timeframe="1D",
        warmup_bars=SMA_SLOW + 5,  # 205
        max_positions=4,
        gross_cap=0.20,
        per_symbol_cap=0.07,
        expected_holding_bars=20,
        asset_class="equity",
        venue="alpaca",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="equity_vol_expansion_pocket_pivot",
        product_class="equity",
        hold_overnight=True,
        profit_target_r=None,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < SMA_SLOW + 1:
            return None
        last = bars[-1]

        sma10 = _sma(bars, SMA_FAST)
        sma50 = _sma(bars, SMA_MID)
        sma200 = market_view.ma_200 if is_finite(market_view.ma_200) else _sma(bars, SMA_SLOW)
        if sma10 is None or sma50 is None or sma200 is None:
            return None
        # Uptrend + regime gate: above all three SMAs.
        if not (last.close > sma10 and last.close > sma50 and last.close > sma200):
            return None

        # Upper-third close within the bar's range.
        bar_range = last.high - last.low
        if bar_range <= 0.0:
            return None
        if last.close < last.low + UPPER_RANGE_FRAC * bar_range:
            return None

        # Volume expansion vs the 50-bar volume SMA.
        vol_sma50 = _vol_sma(bars, VOL_MA)
        if vol_sma50 is None or vol_sma50 <= 0.0:
            return None
        if last.volume <= VOL_MULT * vol_sma50:
            return None

        # Pocket-pivot accumulation signature: this UP day's volume exceeds the
        # largest DOWN-day volume of the prior 10 bars.
        if last.close <= last.open:
            return None  # must be an up day
        max_down_vol = _max_down_volume(bars, DOWN_VOL_LOOKBACK)
        if last.volume <= max_down_vol:
            return None

        vol_ratio = last.volume / vol_sma50
        scored = STRENGTH_FLOOR + VOL_STRENGTH_GAIN * (vol_ratio - VOL_MULT)
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"pocket_pivot+vol_ratio={vol_ratio:.2f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "sma_10": f"{sma10:.4f}",
                "sma_50": f"{sma50:.4f}",
                "sma_200": f"{sma200:.4f}",
                "vol_ratio": f"{vol_ratio:.2f}",
                "max_down_vol_10": f"{max_down_vol:.1f}",
            },
        )


__all__ = [
    "ATR_TRAIL_MULT",
    "DOWN_VOL_LOOKBACK",
    "EquityVolExpansionPocketPivotStrategy",
    "SMA_FAST",
    "SMA_MID",
    "SMA_SLOW",
    "STRENGTH_FLOOR",
    "TTL_BARS",
    "UPPER_RANGE_FRAC",
    "VOL_MA",
    "VOL_MULT",
    "VOL_STRENGTH_GAIN",
]
