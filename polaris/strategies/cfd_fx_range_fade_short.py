"""cfd_fx_range_fade_short — Capital CFD FX majors, SHORT-only range fade.

THESIS-FIRST (BG3 parallel manual archetype track, 2026-07-10): fills the
structural SHORT gap the current survivor roster leaves open. Every REGISTERED
CFD trend strategy (``gold_breakout_1h`` / ``gold_trend_chandelier_1d`` /
``gold_riskoff_trend_amplify`` / ``index_52w_high_momentum`` /
``silver_breakout_1h`` / ``us100_breakout_1h`` / ``uk100_breakout_1h``) is
LONG-only; the two registered bidirectional strategies
(``fx_breakout_basket`` / ``xau_indices_trend``) are TREND-continuation, not
mean-reversion. Zero registered strategies fade an overbought extreme SHORT.

This is NOT a blind-sweep invention — it mirrors the ALREADY-OBSERVED
``fx_range_fade`` thesis (module preserved read-only,
``polaris/strategies/fx_range_fade.py``): the ONLY net-price-positive
strategy in the 2026-06-24 live diagnosis (+0.251R, 41% MFE capture —
``vault/50_research/strategy_vs_execution_partA_2026-06-24.md`` /
``vault/50_research/all_strategy_edge_diagnosis_2026-06-24.md``), killed by
taker-fee-on-leverage economics ("fee가 GOOD 신호 살해"), NOT by a bad signal.
This module re-authors JUST the SHORT half of that same fade thesis as a
FRESH strategy_id — deliberately NOT re-registering the old dead module
(that would silently reverse a Jin-driven KILL without review) and
deliberately NOT the LONG half either (single-purpose, cleanly testable).

Trigger: ADX_14 < adx_range_max (no-trend / range confirmation — same trend
guard fx_range_fade used) AND close >= bb_upper (overbought extreme) -> SHORT,
targeting bb_middle. correlation_group_id carries "range" -> REVERSION exit
bucket (bounded revert-to-mean, NOT let-winners-run — see
``polaris/core/live_recalc/exit_thesis.py:_REVERSION_GROUP_SUBSTRINGS``).

PENDING P0a GATE: NOT in ``STRATEGY_REGISTRY`` — ``dispatch_eligible=False``
is documentary only (registry membership, not this flag, is what the live
dispatcher iterates on). Registration requires the P0a evolve engine
(``polaris.core.evolve``) to clear the honest-N gate on real DB bars first
(no manual eyeball promotion — the CS-3/anti-eyeball-gating mandate), THEN a
fresh Claude sub-agent external review + live-firing adversarial check
(registered != dispatched).

Verified params are named Final constants (no magic numbers):
  - ``ADX_RANGE_MAX = 25.0`` (Wilder no-trend convention; P0a-varyable, see
    ``polaris.core.evolve.param_bounds.PARAM_BOUNDS['cfd_fx_range_fade_short']``)
  - ``BB_WINDOW = 20`` / ``BB_STD = 2.0`` (via ``MarketView.bb_upper`` /
    ``bb_middle`` — pre-fed, not recomputed in-module)
  - ``FADE_TARGET_R = 1.0`` (same coarse extreme-to-middle heuristic
    fx_range_fade documents: 1R ~= 2 sigma(20) ~= 2*ATR(14), correlated not
    equal; a future refinement derives the target per-position from the
    actual bb_extreme -> bb_middle distance in R)
"""

from __future__ import annotations

from typing import Final

from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)
from polaris.strategies.fx_breakout_basket import (
    BASKET_SYMBOLS,
    _normalize_basket_symbol,
)

# ADX ceiling confirming "no trend" (range regime) -- the trend guard that
# keeps the fade OUT of a runaway move (fade fires adx < max, adx >= max
# yields to the trend strategies). P0a-varyable ENTRY-trigger knob.
ADX_RANGE_MAX: Final[float] = 25.0

# Bounded revert-to-mean harvest (R). See module docstring CALIBRATION note.
FADE_TARGET_R: Final[float] = 1.0

STRENGTH_BASE: Final[float] = 0.5
ADX_FACTOR_WEIGHT: Final[float] = 0.3
OVERSHOOT_WEIGHT: Final[float] = 0.2
TTL_BARS: Final[int] = 4
LEVERAGE_MAX: Final[float] = 30.0
WARMUP_BARS: Final[int] = 30


class CFDFXRangeFadeShortStrategy(BaseStrategy):
    # Varyable ENTRY-trigger knob (P0a). Class default == module constant ==
    # frozen baseline -> behavior-0 for default instances.
    adx_range_max: float = ADX_RANGE_MAX
    # ttl_bars intentionally NOT in PARAM_BOUNDS (inert-in-replay, matches the
    # rest of the roster's convention). Behavior-0.
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="cfd_fx_range_fade_short",
        timeframe="1H",
        warmup_bars=WARMUP_BARS,
        max_positions=4,
        gross_cap=0.18,
        per_symbol_cap=0.06,
        expected_holding_bars=6,
        asset_class="fx",
        venue="capital",
        # "range" substring -> REVERSION exit bucket (bounded target, not
        # let-winners-run).
        correlation_group_id="cfd_fx_range_fade_short",
        product_class="cfd",
        profit_target_r=FADE_TARGET_R,
        # PENDING P0a honest-N gate; NOT registered in STRATEGY_REGISTRY (see
        # module docstring). Documentary only while unregistered.
        dispatch_eligible=False,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        if _normalize_basket_symbol(market_view.symbol) not in BASKET_SYMBOLS:
            return None
        bars = market_view.bars
        if not bars:
            return None
        if not (
            is_finite(market_view.adx_14)
            and is_finite(market_view.bb_upper)
            and is_finite(market_view.bb_lower)
            and is_finite(market_view.bb_middle)
        ):
            return None
        adx = market_view.adx_14
        if adx is None or adx >= self.adx_range_max:
            return None
        upper = market_view.bb_upper
        lower = market_view.bb_lower
        if upper is None or lower is None or upper <= lower:
            return None
        last = bars[-1]
        close = last.close
        if close < upper:
            return None  # not at the overbought extreme -- no fade
        half = (upper - lower) / 2.0
        overshoot = (close - upper) / half if half > 0.0 else 0.0
        adx_factor = (self.adx_range_max - adx) / self.adx_range_max  # 0..1
        strength = min(
            1.0,
            max(
                STRENGTH_BASE,
                STRENGTH_BASE
                + ADX_FACTOR_WEIGHT * adx_factor
                + OVERSHOOT_WEIGHT * min(overshoot, 1.0),
            ),
        )
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="short",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"fx_range_fade_short+adx={adx:.1f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={
                "adx_14": f"{adx:.1f}",
                "bb_upper": f"{upper:.5f}",
                "bb_mid": f"{market_view.bb_middle:.5f}",
                "leverage": f"{int(LEVERAGE_MAX)}",
            },
        )


__all__ = [
    "ADX_RANGE_MAX",
    "CFDFXRangeFadeShortStrategy",
    "FADE_TARGET_R",
    "LEVERAGE_MAX",
    "STRENGTH_BASE",
    "TTL_BARS",
    "WARMUP_BARS",
]
