"""equity_xsect_52w_momentum — Alpaca US equity, daily 52-week (252-bar) prior-
high breakout with 2-consecutive-close PERSISTENCE + ATR follow-through
confirm. DEMO/PAPER virtual, no real capital / fees.

Spec source: ``vault`` Alpaca-sleeve build spec §1 #2 (디베이트 R2 수렴 로스터).

Signaling-strategy contract (ADR-008): entry trigger ONLY. Exit is G7 trend-
break (Donchian-20 prior-low, documented downstream, not applied here).
``correlation_group_id`` carries no reversion substring → TREND let-run
bucket; ``hold_overnight=True``; ``profit_target_r=None`` (winners run
unbounded); ``expected_holding_bars=20``. flow_not_block: a clean trigger
ALWAYS emits.

ENTRY (1d bar close, deterministic, no look-ahead) requires ALL:
  (1) ``equity_liquidity_ok(bars, 1)`` (T1+T2 value-based liquidity floor —
      §0d, no ticker hardcode);
  (2) ``prior_high252 = max(high[bars[-254:-2]])`` (252-bar / 52-week prior
      high, window EXCLUDES BOTH confirm bars — no look-ahead through the
      persistence check itself);
  (3) **2-consecutive-close persistence** — ``bars[-2].close > prior_high252``
      AND ``bars[-1].close > prior_high252``. A single-tick breakout that
      reverts the very next bar must NOT fire (원샷 틱 진입 금지 — a lone spike
      is noise, not a confirmed regime break);
  (4) **ATR follow-through confirm** — ``(bars[-1].close - prior_high252) >=
      FOLLOW_ATR_MULT(0.25) * ATR14`` (Wilder, in-module — the breakout must
      clear the prior high by a MEANINGFUL multiple of current volatility,
      not just a few cents).

Strength scales with the follow-through excess in ATR units:
``strength = min(1.0, 0.5 + 4.0 * (excess / ATR14))``, floored + capped —
EXPECTANCY-positive size, never a dampen.

Wilder ATR(14) is NOT pre-fed in ``MarketView`` — recomputed in-module from
the bar series (same seeded-SMA + recursive-smoothing shape as
``supertrend._atr_at_last``).

Verified params are named Final constants (no magic numbers):
  - ``PRIOR_HIGH_LOOKBACK = 252`` / ``CONFIRM_LAG = 2`` / ``ATR_PERIOD = 14``
  - ``FOLLOW_ATR_MULT = 0.25``
"""

from __future__ import annotations

from typing import Final

from polaris.strategies._equity_liquidity import equity_liquidity_ok
from polaris.strategies._virtual_loosen import virtual_loosen
from polaris.strategies.base import (
    BarView,
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    make_signal_id,
)

PRIOR_HIGH_LOOKBACK: Final[int] = 252  # ~52 trading weeks
CONFIRM_LAG: Final[int] = 2  # bars[-2] & bars[-1] persistence window
ATR_PERIOD: Final[int] = 14
FOLLOW_ATR_MULT: Final[float] = 0.25
# Minimum bars needed to slice bars[-(PRIOR_HIGH_LOOKBACK+CONFIRM_LAG):-CONFIRM_LAG]
# without clamping (254). metadata.warmup_bars (270, table-pinned) already
# exceeds this — kept as an explicit redundant guard (degrade-never-crash).
MIN_BARS_REQUIRED: Final[int] = PRIOR_HIGH_LOOKBACK + CONFIRM_LAG

# Strength curve (frozen v1): deeper ATR-normalized follow-through -> stronger.
STRENGTH_FLOOR: Final[float] = 0.5
STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3

# §0d value-based liquidity floor (1D -> 1 bar/session).
EQ_LIQ_BARS_PER_DAY: Final[int] = 1

# metadata table value (verbatim, per spec §1) — a fixed generous warmup floor,
# not a formula, so it stays stable regardless of any future param tuning.
WARMUP_BARS: Final[int] = 270


def _wilder_atr14(bars: list[BarView]) -> float:
    """Wilder ATR(``ATR_PERIOD``) at the last bar (``supertrend._atr_at_last``
    pattern: true range series, SMA-seeded, then recursively smoothed)."""
    n = len(bars)
    tr: list[float] = [bars[0].high - bars[0].low]
    for i in range(1, n):
        h, low_, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        tr.append(max(h - low_, abs(h - prev_close), abs(low_ - prev_close)))
    if n < ATR_PERIOD:
        return sum(tr) / n
    atr = sum(tr[:ATR_PERIOD]) / ATR_PERIOD
    for i in range(ATR_PERIOD, n):
        atr = (atr * (ATR_PERIOD - 1) + tr[i]) / ATR_PERIOD
    return atr


class EquityXsect52wMomentumStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="equity_xsect_52w_momentum",
        timeframe="1D",
        warmup_bars=WARMUP_BARS,
        max_positions=3,
        gross_cap=0.12,
        per_symbol_cap=0.05,
        expected_holding_bars=20,
        asset_class="equity",
        venue="alpaca",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="equity_xsect_52w_momentum",
        product_class="equity",
        hold_overnight=True,
        profit_target_r=None,
        dispatch_eligible=virtual_loosen(True, False),
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < MIN_BARS_REQUIRED:
            return None
        if not equity_liquidity_ok(bars, EQ_LIQ_BARS_PER_DAY):
            return None

        # prior_high252 EXCLUDES both confirm bars (bars[-2], bars[-1]) — no
        # look-ahead through the persistence window itself.
        window = bars[-(PRIOR_HIGH_LOOKBACK + CONFIRM_LAG):-CONFIRM_LAG]
        prior_high252 = max(b.high for b in window)

        prev_bar, last = bars[-2], bars[-1]
        # 2-consecutive-close persistence — a single-tick breakout that
        # reverts the very next bar must NOT fire.
        if prev_bar.close <= prior_high252:
            return None
        if last.close <= prior_high252:
            return None

        atr14 = _wilder_atr14(bars)
        if atr14 <= 0.0:
            return None
        excess = last.close - prior_high252
        if excess < FOLLOW_ATR_MULT * atr14:
            return None

        excess_atr = excess / atr14
        scored = STRENGTH_FLOOR + STRENGTH_GAIN * excess_atr
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"equity_xsect_52w_momentum+excess_atr={excess_atr:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "prior_high_252": f"{prior_high252:.4f}",
                "atr_14": f"{atr14:.4f}",
                "excess_atr": f"{excess_atr:.4f}",
            },
        )


__all__ = [
    "ATR_PERIOD",
    "CONFIRM_LAG",
    "EQ_LIQ_BARS_PER_DAY",
    "EquityXsect52wMomentumStrategy",
    "FOLLOW_ATR_MULT",
    "MIN_BARS_REQUIRED",
    "PRIOR_HIGH_LOOKBACK",
    "STRENGTH_FLOOR",
    "STRENGTH_GAIN",
    "TTL_BARS",
    "WARMUP_BARS",
]
