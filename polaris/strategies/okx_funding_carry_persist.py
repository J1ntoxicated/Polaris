"""okx_funding_carry_persist — OKX SPOT, persistent-funding CARRY (long-only).

THESIS-FIRST (BG3 parallel manual archetype track, 2026-07-10): fills the
structural CARRY gap. The ONLY strategy currently consuming OKX perp funding
is ``weekend_funding_capitulation_maker`` — a MEAN-REVERSION squeeze-unwind
(SYMBOL-RELATIVE p10 percentile, weekend-only, bounded +1R harvest over
24-48h). No strategy harvests the funding differential ITSELF as a genuine
carry: OKX SPOT is long-only, so the tradeable carry leg is "be long while
shorts pay longs" (funding < 0) -- but as a PERSISTENT, all-week, ABSOLUTE-
threshold exposure, not a percentile-extreme bounce.

This is deliberately a DIFFERENT mechanism from the capitulation maker, not a
duplicate:
  * ABSOLUTE threshold (moderate, design-bounded) vs SYMBOL-RELATIVE p10
    percentile -- robust to a thin funding-rate-history (no p10 dependency).
  * All 7 UTC weekdays vs weekend-only.
  * Let the carry run (``profit_target_r=None``, TREND exit bucket) vs a
    bounded +1R squeeze-unwind harvest.
Both may co-fire on the same bar (a deep p10 breach is also below the mild
carry threshold) -- by design, the same "compete, arbitration ranks
downstream" precedent ``fx_range_fade`` documents for its own ADX overlap
with ``fx_breakout_basket``.

Trigger: ``market_view.altdata.funding_rate_symbol <= funding_threshold``
(moderate negative -- shallower than the capitulation maker's observed
extreme examples of -0.0019 to -0.0030, see
``tests/test_weekend_funding_capitulation_maker.py``) -> SPOT LONG on the
tradeable spot symbol (never the perp). funding = SIGNAL only.

PENDING P0a GATE: NOT in ``STRATEGY_REGISTRY`` -- ``dispatch_eligible=False``
is documentary only (registry membership, not this flag, is what the live
dispatcher iterates on). Registration requires the P0a evolve engine to
clear the honest-N gate on real DB bars first (no manual eyeball promotion),
THEN a fresh Claude sub-agent external review + live-firing adversarial
check (registered != dispatched).

Verified params are named Final constants (no magic numbers):
  - ``FUNDING_THRESHOLD = -0.0003`` (moderate persistent-negative funding;
    P0a-varyable, see
    ``polaris.core.evolve.param_bounds.PARAM_BOUNDS['okx_funding_carry_persist']``)
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

# Moderate persistent-negative funding threshold (decimal fraction, OKX
# fundingRate convention -- e.g. -0.0003 = -0.03% per 8h funding interval).
# Shallower than an extreme percentile breach by design: the carry thesis
# harvests the ORDINARY funding differential, not a squeeze-unwind bounce.
FUNDING_THRESHOLD: Final[float] = -0.0003

STRENGTH_FLOOR: Final[float] = 0.4
STRENGTH_OFFSET: Final[float] = 0.5
STRENGTH_DEPTH_CAP: Final[float] = 0.5
TTL_BARS: Final[int] = 6
WARMUP_BARS: Final[int] = 24


class OKXFundingCarryPersistStrategy(BaseStrategy):
    # Varyable ENTRY-trigger knob (P0a). Class default == module constant ==
    # frozen baseline -> behavior-0 for default instances.
    funding_threshold: float = FUNDING_THRESHOLD
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="okx_funding_carry_persist",
        timeframe="1H",
        warmup_bars=WARMUP_BARS,
        max_positions=6,
        gross_cap=0.18,
        per_symbol_cap=0.06,
        expected_holding_bars=48,  # let the carry run, longer than the maker's 36
        asset_class="spot",
        venue="okx",
        # NO reversion/range/mean_reversion substring -> TREND exit bucket
        # (let-run; the carry thesis wants exposure while funding persists,
        # not a bounded single unwind harvest).
        correlation_group_id="okx_funding_carry_persistence",
        hold_overnight=True,
        profit_target_r=None,
        # bars-EXTERNAL input: funding refreshes on its own intraday cadence
        # independent of the 1H bar close -- the bar-advance dispatch gate
        # must not suppress a re-eval (compute-scheduling exemption only, see
        # StrategyMetadata.evaluates_in_progress_bar).
        evaluates_in_progress_bar=True,
        # PENDING P0a honest-N gate; NOT registered in STRATEGY_REGISTRY (see
        # module docstring). Documentary only while unregistered.
        dispatch_eligible=False,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if not bars:
            return None
        last = bars[-1]
        funding = market_view.altdata.funding_rate_symbol
        if not is_finite(funding) or funding is None:
            return None
        if funding >= self.funding_threshold:
            return None
        # Depth past the threshold -> strength (capped). threshold is
        # negative here; denom guards a degenerate threshold == 0.
        denom = abs(self.funding_threshold) if self.funding_threshold != 0.0 else 1.0
        depth = min(STRENGTH_DEPTH_CAP, (self.funding_threshold - funding) / denom)
        strength = min(1.0, max(STRENGTH_FLOOR, depth + STRENGTH_OFFSET))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,  # tradeable SPOT symbol -- never the perp.
            side="long",  # funding = signal; trade = SPOT long (long-only).
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"funding_carry fr={funding:.5f}<{self.funding_threshold:.5f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={"funding": f"{funding:.6f}"},
        )


__all__ = [
    "FUNDING_THRESHOLD",
    "OKXFundingCarryPersistStrategy",
    "STRENGTH_FLOOR",
    "TTL_BARS",
    "WARMUP_BARS",
]
