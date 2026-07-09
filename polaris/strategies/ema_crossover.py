"""EMA Crossover — OKX SPOT, 1H, long-only (correlation_group=spot_ema_trend).

The 61%-dead-entry diagnosis (MFE<0.1R) says entries lacked a real trend edge.
This is the most-deployed trend template: a fast/slow EMA cross, but only TAKEN
when it agrees with the dominant trend (close above EMA200 regime filter) and the
tape is actually trending (ADX_14 above a floor). The three filters together turn
the raw cross — which whipsaws in chop — into a confirmed trend-continuation
entry, attacking the dead-entry rate at its source.

Net-new vs the existing OKX trend pair: ``tsmom`` fires on a RETURN level
(momentum_20bar>0, no cross / no regime gate) and ``spot_donchian`` on a CHANNEL
break (close>donchian_high_40). This fires on a fast/slow EMA CROSS inside the
EMA200 trend — a distinct trigger geometry, so it adds breadth without dupes.

Long-only (OKX SPOT crypto is LONG-ONLY; US-restricted alts blocklisted upstream).
EMA200 keeps it on the right side of the dominant trend, so a bull cross fires;
a bear cross is simply no-emit (no short side to take).

Data: EMA fast/slow are computed in-strategy from the close series (self-contained,
like tsmom computes its own momentum) — the orchestrator-fed ``ema_20``/``ema_50``
emphasis fields are crypto-None, so the strategy never depends on them. The EMA200
regime uses the orchestrator's ``ma_200`` when finite (it IS populated on the spot
stream) and falls back to an in-strategy compute otherwise. ``adx_14`` is the
already-computed trend-strength field consumed directly.

Trigger (all true, on the just-closed bar):
  * fast EMA crossed UP through slow EMA THIS bar (prev fast<=slow, now fast>slow)
  * close > EMA200            (regime: dominant uptrend)
  * adx_14 > ADX_THRESHOLD    (trend strength: not chop)

Harvest (profit_target_r): a fixed +R take-profit so a real trend winner is BANKED
before the let-winners-run ATR trail can round-trip it (the give-back bug the
diagnosis flags — only 29% of trades reached +0.3R, MFE was left on the table).
The exit engine takes this target FIRST, while the ATR-trail still lets the move
run up to it. EXPECTANCY — a per-position close target, never a size/entry throttle.

P0 params:
  - ``ema_fast = 20`` / ``ema_slow = 50``
  - ``ema_regime = 200``
  - ``adx_threshold = 20``
  - ``profit_target_r = 2.0``
"""

from __future__ import annotations

from polaris.strategies._virtual_loosen import virtual_loosen
from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)

EMA_FAST = 20
EMA_SLOW = 50
# VIRTUAL-mode loosening (Jin 2026-07-07): 200->50-EMA regime filter fires
# sooner (cross+regime+ADX mechanism unchanged). Deepened 50->20 (Jin
# 2026-07-08): still a real regime-EMA filter (floor>=20), just faster to
# confirm. REAL byte-identical.
EMA_REGIME = virtual_loosen(20, 200)
ADX_THRESHOLD = 14.0  # relaxed 20 -> 14 (flow_not_block, more emits): a weaker-trend EMA cross now fires

# Strength curve (frozen v1): a stronger trend (higher ADX) → stronger signal.
STRENGTH_BASE = 0.5
ADX_STRENGTH_DENOM = 40.0
TTL_BARS = 6

# Take-profit target (R). An EMA-cross trend winner is harvested at +2R so a real
# move is BANKED before the wide let-winners-run trail (2 ATR off the peak) can
# round-trip it back to ~0R (the give-back bug: the diagnosis showed MFE left on
# the table, only 29% of trades reached +0.3R). 2.0 lets the winner RUN — it is a
# generous trend target, not a mean-reversion clip — while still capping the
# give-back. Consumed by the precise-exit engine via metadata.profit_target_r.
# EXPECTANCY, not a throttle: a per-position close only — size/entry/halt untouched.
EMA_TREND_TARGET_R = 2.0


def _ema(closes: list[float], n: int) -> float:
    """Exponential moving average of the close series (full-history seed).

    Seeds on the first close and smooths forward with ``k = 2/(n+1)`` — the
    standard EMA recursion. Returns the EMA value at the LAST bar.
    """
    if not closes:
        return 0.0
    k = 2.0 / (n + 1.0)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1.0 - k)
    return ema


class EMACrossoverStrategy(BaseStrategy):
    # Varyable ENTRY-trigger knob (P0a). Class default == module constant ==
    # frozen baseline → behavior-0 for default instances.
    adx_threshold: float = ADX_THRESHOLD
    # ttl_bars intentionally NOT in PARAM_BOUNDS (inert-in-replay). Behavior-0.
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="ema_crossover",
        timeframe=virtual_loosen("15m", "1H"),
        warmup_bars=EMA_SLOW + 5,
        max_positions=3,
        gross_cap=0.20,
        per_symbol_cap=0.07,
        expected_holding_bars=24,
        asset_class="spot",
        venue="okx",
        correlation_group_id="spot_ema_trend",
        profit_target_r=EMA_TREND_TARGET_R,
        # KILL fee-fatal (2026-06-28). The #56 stop-bleeders autopsy KILLed
        # rsi_bb_pullback for a confirmed fee-fatal edge; ema_crossover was grouped
        # into the same cull but Jin DEFERRED its call pending its own live read.
        # That read landed: the 1H crypto cross is gross-positive but fee-fatal —
        # gross +$0.12 per round-trip < the OKX taker fee $2.37, so the edge never
        # clears the fee. So it now joins the no-emit KILL set, the SAME dispatch-
        # level pattern as rsi_bb_pullback: dispatch_eligible=False means
        # generate_raw_signal is never called (no NEW entry), while the module +
        # the open-position close path stay REGISTERED (KILL != removal — open
        # positions still exit via the recalc loop). flow_not_block-safe: an edgeless
        # strategy is retired at the source, it neither halts nor dampens any other
        # strategy's size.
        # VIRTUAL-mode loosening (Jin 2026-07-07): the KILL was for fee-fatality
        # ONLY (gross +$0.12 < the real OKX taker fee $2.37) — virtual has no
        # real fees, so un-KILL (dispatch_eligible=True) in virtual mode; this is
        # the actual unleash for this strategy (0 -> live). REAL stays the
        # byte-identical KILL (env unset).
        dispatch_eligible=virtual_loosen(True, False),
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < EMA_SLOW + 1:
            return None
        last = bars[-1]
        closes = [b.close for b in bars]

        # 1. Confirmed bull CROSS this bar: the fast EMA was at/below the slow EMA
        #    on the prior bar and is now above it. A cross (not a mere fast>slow
        #    level) is the entry edge — it marks the trend INFLECTION, not a
        #    late chase deep into an already-extended move.
        fast_now = _ema(closes, EMA_FAST)
        slow_now = _ema(closes, EMA_SLOW)
        fast_prev = _ema(closes[:-1], EMA_FAST)
        slow_prev = _ema(closes[:-1], EMA_SLOW)
        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        if not crossed_up:
            return None

        # 2. EMA_REGIME regime: only take the cross on the right side of the
        #    dominant trend. Prefer the orchestrator-fed ma_200 (populated on the
        #    spot stream) ONLY when EMA_REGIME is still 200 (REAL mode — the
        #    pre-fed field is a 200-period MA, not valid once VIRTUAL mode
        #    loosens EMA_REGIME to 50); fall back to an in-strategy compute
        #    otherwise.
        if EMA_REGIME == 200 and is_finite(market_view.ma_200):
            ema200 = market_view.ma_200
        elif len(closes) >= EMA_REGIME:
            ema200 = _ema(closes, EMA_REGIME)
        else:
            return None  # not enough history to confirm the regime — no-emit
        if ema200 is None or last.close <= ema200:
            return None

        # 3. ADX trend-strength gate: skip the cross when the tape is chopping
        #    (low ADX) — that is where EMA crosses whipsaw.
        if not is_finite(market_view.adx_14):
            return None
        adx = market_view.adx_14
        if adx is None or adx <= self.adx_threshold:
            return None

        adx_score = STRENGTH_BASE + (adx - self.adx_threshold) / ADX_STRENGTH_DENOM
        strength = min(1.0, max(STRENGTH_BASE, adx_score))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"ema_cross_{EMA_FAST}x{EMA_SLOW}+adx={adx:.1f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "adx_14": f"{adx:.1f}",
                "ema_fast": f"{fast_now:.4f}",
                "ema_slow": f"{slow_now:.4f}",
                "ema_200": f"{ema200:.4f}",
            },
        )


__all__ = [
    "ADX_STRENGTH_DENOM",
    "ADX_THRESHOLD",
    "EMA_FAST",
    "EMA_REGIME",
    "EMA_SLOW",
    "EMA_TREND_TARGET_R",
    "EMACrossoverStrategy",
    "STRENGTH_BASE",
    "TTL_BARS",
]
