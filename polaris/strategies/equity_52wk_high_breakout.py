"""equity_52wk_high_breakout — Alpaca US equity, 52-week-high breakout (let-run).

Spec source: research selection ``equity_52wk_high_breakout`` (rank-4, DEMO/PAPER,
NET +68bps verified, both OOS halves + (+63/+74bps), 1250-trade backtest). The
George/Hwang anchoring-continuation edge on liquid large-caps: buy within 1% of (or
making) a new 52-week high INSIDE a confirmed uptrend (close > SMA200), and let the
new-high leg run on a wide chandelier. Fills the empty equity 'breakout' cell.
Distinct from the KILLed ``equity_tsmom`` (bare 20-bar momentum) and from
``connors_rsi2`` (RSI(2) reversion, profit_target_r=1.0). Alpaca is commission-free
(ALPACA_REAL_BPS=0) so only ~1-3bps large-cap spread to clear.

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
The verified asymmetric let-winners-run EXIT (hard stop entry-2.0*ATR(20) + a
3.0*ATR(20) chandelier trail ratcheting from the running peak, re-armed on each new
high / +0.5R MFE) is owned by the G5/G7 gates via ``StrategyMetadata`` (TREND
bucket — ``correlation_group_id`` has no reversion substring → let-winners-run;
``hold_overnight=True``; ``profit_target_r=None`` so winners run unbounded;
``expected_holding_bars=15`` — median hold 15 / p90 38 trading days = genuine
multi-week holds, the OPPOSITE of the KILLed equity_tsmom's premature cuts). The
edge is fat-tailed by design (top5% ~158% of pnl, payoff 1.92, win 41%) = the
mandated loss/profit asymmetry; survives dropping the top-20 winners (+16bps). The
-1.0R rail is load-bearing. flow_not_block.

ENTRY (1d bar close, LONG-only): emit when ``close >= 0.99 * max(high[prior 252
bars])`` (within 1% of, or making, a new 52-week high) AND ``close > SMA(200)``
(regime filter — only buy strength in confirmed uptrends). The 252-bar window
EXCLUDES the current closing bar (no look-ahead). The 252-bar high is NOT pre-fed →
recompute in-module; SMA(200) prefers ``market_view.ma_200`` when finite, else
``_sma(bars, 200)`` (the ``connors_rsi2`` SMA pattern). The liquidity filter keeps
junk symbols out UPSTREAM — the strategy itself does not block.

INERT until the Alpaca SIP key (#42) routes equity bars (degrade-never-crash: an
un-routed symbol → no bars → no emit, never a crash).

Verified params are named Final constants (no magic numbers):
  - ``HIGH_LOOKBACK = 252`` / ``PROXIMITY = 0.99`` / ``TREND_MA = 200``
  - ``ATR_STOP_MULT = 2.0`` / ``ATR_TRAIL_MULT = 3.0`` (G7-consumed exit basis)
"""

from __future__ import annotations

from typing import Final

from polaris.strategies._virtual_loosen import virtual_loosen
from polaris.strategies.base import (
    BarView,
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)

HIGH_LOOKBACK: Final[int] = 252
PROXIMITY: Final[float] = 0.99
TREND_MA: Final[int] = 200
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
ATR_STOP_MULT: Final[float] = 2.0
ATR_TRAIL_MULT: Final[float] = 3.0

# Strength curve (frozen v1). Scales with how decisively the new high clears the
# prior 252-bar high, floored + capped. EXPECTANCY-positive size, never a dampen.
STRENGTH_FLOOR: Final[float] = 0.5
BREAKOUT_STRENGTH_GAIN: Final[float] = 8.0
TTL_BARS: Final[int] = 3


def _sma(bars: list[BarView], window: int) -> float | None:
    """Simple moving average of the last ``window`` closes (None if too few)."""
    if window <= 0 or len(bars) < window:
        return None
    total = 0.0
    for bar in bars[-window:]:
        total += bar.close
    return total / window


class Equity52WkHighBreakoutStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="equity_52wk_high_breakout",
        timeframe="1D",
        warmup_bars=HIGH_LOOKBACK + 1,  # 253
        max_positions=4,
        gross_cap=0.20,
        per_symbol_cap=0.07,
        expected_holding_bars=15,
        asset_class="equity",
        venue="alpaca",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="equity_52wk_high_breakout",
        product_class="equity",
        hold_overnight=True,
        profit_target_r=None,
        # P3 promotion (2026-07-16, vault/50_research/built-not-wired-audit.md):
        # formalizes the prior ad hoc VIRTUAL-only re-admit — see
        # session_breakout.py's identical note + polaris/strategies/__init__.py.
        dispatch_eligible=virtual_loosen(True, False),
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
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
        # Within 1% of (or making) the 52-week high.
        if last.close < PROXIMITY * prior_high_252:
            return None

        # SMA(200) regime filter — prefer the pre-fed ma_200, else compute.
        sma200 = market_view.ma_200 if is_finite(market_view.ma_200) else _sma(bars, TREND_MA)
        if sma200 is None or sma200 <= 0.0 or last.close <= sma200:
            return None

        breakout_frac = last.close / prior_high_252 - 1.0
        scored = STRENGTH_FLOOR + BREAKOUT_STRENGTH_GAIN * max(0.0, breakout_frac)
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"equity_52wk_high+brk={breakout_frac:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "high_252": f"{prior_high_252:.4f}",
                "sma_200": f"{sma200:.4f}",
                "breakout_frac": f"{breakout_frac:.4f}",
            },
        )


__all__ = [
    "ATR_STOP_MULT",
    "ATR_TRAIL_MULT",
    "BREAKOUT_STRENGTH_GAIN",
    "Equity52WkHighBreakoutStrategy",
    "HIGH_LOOKBACK",
    "PROXIMITY",
    "STRENGTH_FLOOR",
    "TREND_MA",
    "TTL_BARS",
]
