"""Connors RSI(2) Pullback — Alpaca US equity (Track C), mean-reversion.

The Connors ultra-short RSI(2) system: buy the short-term oversold dip INSIDE a
confirmed uptrend, hold for the bounded snap-back. Documented 65-75% win on
liquid large-caps / indices. Distinct from ``equity_rsi_bb_pullback`` — that is
the RSI(14) + Bollinger fade; this is the RSI(2) + SMA(200) trend-filtered dip.

Trigger (long): ``rsi_2 < 10`` AND ``close > sma_200`` (oversold pullback inside
an uptrend). Long-only — equity stream + OKX long-only respected (no short side).

Exit (downstream, precise-exit engine): ``close > sma_5`` OR ``rsi_2 > 65``. The
edge is a BOUNDED revert (oversold → mean), so the strategy declares
``profit_target_r=1.0`` and the harvest engine banks it instead of letting the
wide ATR trail round-trip the bounce. EXPECTANCY, never a throttle / block.

RSI(2) / SMA(200) / SMA(5) are computed here from bars (not pre-fed in
MarketView) — trivial, no new indicator infra.

P0 params:
  - ``rsi_period = 2``
  - ``rsi_entry = 10``    (oversold dip)
  - ``trend_filter_ma = 200``
  - ``exit_ma = 5`` / ``rsi_exit = 65``  (downstream exit reference)
"""

from __future__ import annotations

from polaris.strategies.base import (
    BarView,
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    make_signal_id,
)

RSI_PERIOD = 2
RSI_ENTRY = 10.0
TREND_FILTER_MA = 200
EXIT_MA = 5
RSI_EXIT = 65.0

# Strength curve (frozen v1). Deeper oversold (lower RSI(2)) → stronger.
STRENGTH_FLOOR = 0.4
STRENGTH_OFFSET = 0.5
TTL_BARS = 4

# Take-profit (R). The Connors revert is a bounded oversold→mean snap-back; the
# precise-exit engine harvests at +1R rather than letting the let-winners-run ATR
# trail give the bounce back. EXPECTANCY, not a size dampen / entry block.
# Consumed via metadata.profit_target_r.
REVERT_TARGET_R = 1.0


def _sma(bars: list[BarView], window: int) -> float | None:
    """Simple moving average of the last ``window`` closes (None if too few)."""
    if window <= 0 or len(bars) < window:
        return None
    total = 0.0
    for bar in bars[-window:]:
        total += bar.close
    return total / window


def _rsi(bars: list[BarView], period: int) -> float | None:
    """Wilder RSI over the last ``period`` close-to-close deltas.

    Needs ``period + 1`` closes. Returns 100.0 when there is no downside (avg
    loss == 0) — the standard RSI convention.
    """
    if period <= 0 or len(bars) < period + 1:
        return None
    gain_sum = 0.0
    loss_sum = 0.0
    window = bars[-(period + 1):]
    for prev, cur in zip(window[:-1], window[1:], strict=True):
        delta = cur.close - prev.close
        if delta >= 0.0:
            gain_sum += delta
        else:
            loss_sum += -delta
    avg_gain = gain_sum / period
    avg_loss = loss_sum / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class ConnorsRSI2Strategy(BaseStrategy):
    # Varyable ENTRY-trigger knob (P0a). Class default == module constant ==
    # frozen baseline → behavior-0 for default instances.
    rsi_entry: float = RSI_ENTRY
    # ttl_bars intentionally NOT a PARAM_BOUNDS knob (inert-in-replay). Behavior-0.
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="connors_rsi2",
        timeframe="1D",
        warmup_bars=TREND_FILTER_MA + 5,
        max_positions=4,
        gross_cap=0.18,
        per_symbol_cap=0.06,
        expected_holding_bars=4,
        asset_class="equity",
        venue="alpaca",
        correlation_group_id="equity_connors_reversion",
        product_class="equity",
        profit_target_r=REVERT_TARGET_R,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < TREND_FILTER_MA + 1:
            return None
        rsi2 = _rsi(bars, RSI_PERIOD)
        sma200 = _sma(bars, TREND_FILTER_MA)
        if rsi2 is None or sma200 is None:
            return None
        last = bars[-1]
        # Oversold dip INSIDE an uptrend — the two Connors conditions.
        if rsi2 >= self.rsi_entry:
            return None
        if last.close <= sma200:
            return None
        # Strength: deeper oversold (lower RSI(2)) → stronger.
        depth = (self.rsi_entry - rsi2) / self.rsi_entry  # 0..1
        strength = min(1.0, max(STRENGTH_FLOOR, depth + STRENGTH_OFFSET))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"connors_rsi2={rsi2:.1f}<10+sma200",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={"rsi_2": f"{rsi2:.1f}", "sma_200": f"{sma200:.2f}"},
        )


__all__ = [
    "ConnorsRSI2Strategy",
    "EXIT_MA",
    "REVERT_TARGET_R",
    "RSI_ENTRY",
    "RSI_EXIT",
    "RSI_PERIOD",
    "STRENGTH_FLOOR",
    "STRENGTH_OFFSET",
    "TREND_FILTER_MA",
    "TTL_BARS",
]
