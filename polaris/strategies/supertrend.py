"""Supertrend — OKX SPOT, 1H, long-only (correlation_group=spot_supertrend_trend).

ATR-band trend-flip trend follower. The Supertrend indicator places a trailing
band at ``hl2 ± multiplier*ATR``; the FINAL band ratchets (the upper band only
moves down, the lower band only moves up) and the trend FLIPS when close crosses
the active band. ENTRY long = the trend flips UP this bar (close crosses ABOVE
the upper band → the band becomes a trailing LOWER stop under price).

Net-new vs the existing OKX trend pair: ``spot_donchian`` fires on a CHANNEL break
(close>donchian_high_40) and ``ema_crossover`` on a fast/slow MA CROSS. This fires
on the ATR-BAND flip — a distinct trigger geometry (volatility-scaled trailing
band, not a fixed channel or an MA pair), so it adds breadth without dupes.

Long-only (OKX SPOT crypto is LONG-ONLY): only the flip-UP is taken; a flip-DOWN
is simply no-emit (no short side). The band itself IS the trailing stop, so the
exit is the natural band-flip-down — there is NO fixed take-profit
(``profit_target_r=None``): the position rides the trend via the ATR trail and the
let-winners-run MFE-harvest FSM, exactly the existing trend-exit path. Setting a
fixed +R target here would CLIP the trend ride this strategy exists to capture.

Liquidity floor: micro-caps without real range are skipped via ``atr_pct`` (the
24h ATR%), the same floor ``volume_burst`` uses — restricting the strategy to
liquid majors. flow_not_block: a no-emit when the floor is unmet (no signal), not
a size-cut on a fired signal.

Data: ATR and the Supertrend bands are computed in-strategy from the bar series
(self-contained, like ``tsmom`` computes its own momentum and ``ema_crossover``
its own EMAs) — no new MarketView field.

Trigger (on the just-closed bar):
  * trend was DOWN on the prior bar and is UP now (the flip), i.e. close just
    crossed ABOVE the trailing band
  * ``atr_pct >= ATR_FLOOR_PCT``    (liquidity floor: liquid majors only)

P0 params:
  - ``atr_period = 10``
  - ``multiplier = 3.0``
  - ``atr_floor_pct = 0.05%``
  - ``profit_target_r = None``   (trend ride — band-flip-down is the exit)
"""

from __future__ import annotations

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

ATR_PERIOD = 10
MULTIPLIER = 3.0
# 0.05% (fraction) — liquidity floor (liquid majors only). VIRTUAL-mode
# loosening (Jin 2026-07-08): 0.05% -> 0.02% admits more of the universe while
# still excluding true dead-flat micro-caps; the flip trigger itself is
# unchanged. REAL byte-identical.
ATR_FLOOR_PCT = virtual_loosen(0.0002, 0.0005)

# Strength curve (frozen v1): a wider flip gap (close further above the band,
# scaled by ATR) → a more decisive trend turn → stronger signal.
STRENGTH_BASE = 0.5
GAP_STRENGTH_GAIN = 0.5
TTL_BARS = 6


def _supertrend_uptrend_flags(bars: list[BarView]) -> list[bool]:
    """Per-bar Supertrend trend direction (True = uptrend) over the full series.

    Wilder ATR (period ``ATR_PERIOD``) on the true range; basic bands at
    ``hl2 ± MULTIPLIER*ATR``; the FINAL bands ratchet (upper only tightens down
    while in a prior up-context, lower only tightens up) and the trend flips when
    close crosses the active final band. Returns the uptrend flag per bar (the
    first ``ATR_PERIOD`` bars warm up the ATR and default to the seeded trend).
    """
    n = len(bars)
    # True range series.
    tr: list[float] = [bars[0].high - bars[0].low]
    for i in range(1, n):
        h, low_, prev_close = bars[i].high, bars[i].low, bars[i - 1].close
        tr.append(max(h - low_, abs(h - prev_close), abs(low_ - prev_close)))

    # Wilder-smoothed ATR.
    atr: list[float] = [0.0] * n
    seed = sum(tr[:ATR_PERIOD]) / ATR_PERIOD if n >= ATR_PERIOD else sum(tr) / n
    if n >= ATR_PERIOD:
        atr[ATR_PERIOD - 1] = seed
        for i in range(ATR_PERIOD, n):
            atr[i] = (atr[i - 1] * (ATR_PERIOD - 1) + tr[i]) / ATR_PERIOD
        # Back-fill warmup region with the seed so early bands are defined.
        for i in range(ATR_PERIOD - 1):
            atr[i] = seed
    else:
        for i in range(n):
            atr[i] = seed

    final_upper = [0.0] * n
    final_lower = [0.0] * n
    uptrend = [True] * n
    for i in range(n):
        hl2 = (bars[i].high + bars[i].low) / 2.0
        basic_upper = hl2 + MULTIPLIER * atr[i]
        basic_lower = hl2 - MULTIPLIER * atr[i]
        if i == 0:
            final_upper[i] = basic_upper
            final_lower[i] = basic_lower
            uptrend[i] = bars[i].close >= basic_lower
            continue
        prev_close = bars[i - 1].close
        # Final upper band ratchets DOWN unless price broke the prior upper.
        final_upper[i] = (
            basic_upper
            if (basic_upper < final_upper[i - 1] or prev_close > final_upper[i - 1])
            else final_upper[i - 1]
        )
        # Final lower band ratchets UP unless price broke the prior lower.
        final_lower[i] = (
            basic_lower
            if (basic_lower > final_lower[i - 1] or prev_close < final_lower[i - 1])
            else final_lower[i - 1]
        )
        close = bars[i].close
        if uptrend[i - 1]:
            # In an uptrend the lower band is the stop; close below it → flip down.
            uptrend[i] = close > final_lower[i]
        else:
            # In a downtrend the upper band is the cap; close above it → flip up.
            uptrend[i] = close > final_upper[i]
    return uptrend


def _atr_at_last(bars: list[BarView]) -> float:
    """Wilder ATR at the last bar (for the flip-gap strength scale)."""
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


class SupertrendStrategy(BaseStrategy):
    # Varyable ENTRY-trigger knob (P0a). Class default == module constant ==
    # frozen baseline → behavior-0 for default instances.
    atr_floor_pct: float = ATR_FLOOR_PCT
    # ttl_bars intentionally NOT in PARAM_BOUNDS (inert-in-replay). Behavior-0.
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="supertrend",
        timeframe="1H",
        warmup_bars=ATR_PERIOD + 5,
        max_positions=3,
        gross_cap=0.20,
        per_symbol_cap=0.07,
        expected_holding_bars=24,
        asset_class="spot",
        venue="okx",
        correlation_group_id="spot_supertrend_trend",
        # profit_target_r=None (default): trend ride. The Supertrend band IS the
        # trailing stop; the natural exit is the band-flip-down, taken by the
        # let-winners-run ATR-trail + MFE-harvest exit FSM. A fixed +R target would
        # CLIP the trend this strategy exists to ride — so it opts OUT.
        # UNVALIDATED — design rationale only (no OOS / fee-hurdle evidence), so it
        # stays OUT of NEW-ENTRY dispatch (unvalidated live = churn risk). It was
        # already absent from the prior dispatch literal; this flag makes the KILL
        # explicit + structurally enforced (registered, not dispatched).
        # VIRTUAL-mode loosening (Jin 2026-07-08): unvalidated-live churn risk is
        # VOID in virtual (no real capital) — un-KILL so this dispatches and
        # fires. REAL byte-identical.
        dispatch_eligible=virtual_loosen(True, False),
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < ATR_PERIOD + 2:
            return None
        # Liquidity floor: liquid majors only (skip micro-caps without real range).
        if not is_finite(market_view.atr_pct) or market_view.atr_pct < self.atr_floor_pct:
            return None

        flags = _supertrend_uptrend_flags(bars)
        # The flip-UP: trend was DOWN on the prior bar, UP now.
        if not (flags[-1] and not flags[-2]):
            return None

        last = bars[-1]
        atr = _atr_at_last(bars)
        # Strength scales with how decisively the close cleared the band, in ATR
        # units (a wider flip gap = a more convincing turn).
        if atr > 0.0:
            hl2 = (last.high + last.low) / 2.0
            band = hl2 - MULTIPLIER * atr  # the lower (stop) band now under price
            gap_atr = max(0.0, (last.close - band) / atr)
        else:
            gap_atr = 0.0
        strength = min(1.0, max(STRENGTH_BASE, STRENGTH_BASE + GAP_STRENGTH_GAIN * min(gap_atr, 1.0)))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"supertrend_flip_up+atr%={market_view.atr_pct * 100:.2f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "atr_pct": f"{market_view.atr_pct:.5f}",
                "gap_atr": f"{gap_atr:.2f}",
                "multiplier": f"{MULTIPLIER:.1f}",
            },
        )


__all__ = [
    "ATR_FLOOR_PCT",
    "ATR_PERIOD",
    "GAP_STRENGTH_GAIN",
    "MULTIPLIER",
    "STRENGTH_BASE",
    "SupertrendStrategy",
    "TTL_BARS",
]
