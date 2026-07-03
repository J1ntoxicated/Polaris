"""weekend_funding_capitulation_maker — the 2nd weekend OKX edge (positioning).

Spec source: the weekend OKX regime research (task w0p4essz2,
portfolio[weekend_funding_capitulation_maker], build_priority P1). Of the weekend
OKX probes only TWO genuine edges survived: the deployed price-axis flush
(weekend_thin_book_flush_maker) and THIS positioning-axis edge. They are
ORTHOGONAL — the flush catches a spot-led price wick with neutral funding; this
catches a slow-grind crowded-short with NO price wick. Horizons differ too: the
flush reverts in minutes; this unwinds over +24-48h.

The edge: on a WEEKEND (Sat/Sun UTC) bar where a symbol's perp funding is at or
below its OWN p10 (extreme-negative = a crowded-short capitulation, shorts paying
to stay short), the squeeze tends to unwind UP over the following day or two. We
harvest that forward drift with a passive post-only SPOT long.

🚨 SAMPLE-LIMIT CAVEAT (shadow-first): this edge rests on a 95-day SINGLE market
period (~13.5 weekends; OOS ~45d; OKX funding-rate-history ~30-page cap) — it is
suggestive, NOT multi-year (the flush sibling had 104 weeks and is stronger-
grounded). So it ships behind the maker_fill_shadow real-fee accumulation: the
L4/L5 cold cell stays x1.0 NEUTRAL (n_eff < 5) until live maker fills accrue,
which keeps any amplification off until the edge is measured live. A /debate gate
is MANDATORY before live sizing — cross-check (a) maker bid depth, (b) the
bounded vs let-run exit, and (c) the funding threshold (per-ticker p10 vs abs).

Signal generator ONLY (ADR-008): emits ``RawSignal | None``. Lifecycle (entry
routing / exit / rail) is owned by the AI gate pipeline + live-recalc engine:

  * funding = SIGNAL only; the trade is an OKX SPOT LONG (long-only) — the signal
    symbol is the tradeable spot symbol, NEVER the perp.
  * ENTRY routing — the REVERSION correlation group (carries the "mean_reversion"
    routing substring while naming the positioning axis) ⇒ the bar order-mode
    router posts a passive ``post_only`` bid at the touch; ``maker_no_fill_cancel``
    ⇒ a no-fill SKIPs (a missed passive bid = 0 realised cost, never a taker).
  * EXIT — ``profit_target_r`` = 1.0 harvests the bounded squeeze-unwind drift.
    This is the RESEARCH-VALIDATED config; the let-run variant went OOS-negative
    (overfit) and is BANNED. The -1.0R rail is the engine-owned TAKER floor (a
    maker stop could rest unfilled past the rail — never a maker on the rail).
"""

from __future__ import annotations

from datetime import UTC, datetime

from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)

WARMUP_BARS = 24  # parity with the flush sibling (a real 1H bar window)

# Bounded squeeze-unwind harvest target (R). The research-validated exit: median
# +0.83R net real-fee over a +24-48h unwind. A bounded take-profit, NOT a
# let-winners-run (the let-run variant was OOS-negative). The -1.0R rail is the
# engine-owned TAKER floor.
PROFIT_TARGET_R = 1.0

# Strength curve: deeper below p10 → stronger. Scaled by how far the current
# funding sits past the p10 threshold (capped so a single outlier can't saturate).
STRENGTH_FLOOR = 0.4
STRENGTH_OFFSET = 0.5
STRENGTH_DEPTH_CAP = 0.5
TTL_BARS = 6  # the unwind is a multi-bar drift (slower than the flush)

_WEEKEND_UTC_WEEKDAYS = frozenset({5, 6})  # Sat=5, Sun=6


def _is_weekend_utc(ts: int) -> bool:
    """True when the bar's UTC weekday is Saturday or Sunday (pure, from ts)."""
    return datetime.fromtimestamp(ts, tz=UTC).weekday() in _WEEKEND_UTC_WEEKDAYS


class WeekendFundingCapitulationMakerStrategy(BaseStrategy):
    metadata = StrategyMetadata(
        strategy_id="weekend_funding_capitulation_maker",
        timeframe="1H",
        warmup_bars=WARMUP_BARS,
        max_positions=6,
        gross_cap=0.18,
        per_symbol_cap=0.06,
        expected_holding_bars=36,  # +24-48h squeeze unwind (slower than flush)
        asset_class="spot",
        venue="okx",
        # The group NAMES the positioning axis but carries the "mean_reversion"
        # routing substring ⇒ REVERSION bucket ⇒ passive post-only entry mode +
        # bounded revert exit (NOT let-winners-run).
        correlation_group_id="weekend_funding_positioning_mean_reversion",
        # bounded squeeze-unwind harvest (the +1R leg); the -1.0R rail (engine-
        # owned, TAKER) is the floor.
        profit_target_r=PROFIT_TARGET_R,
        # no-fill = CANCEL/skip: the missed passive bid is 0 realised cost, the
        # edge IS the passive fill — never a forced taker fallback.
        maker_no_fill_cancel=True,
        # SHADOW-FIRST ([[weekend_maker_honest_rerun_2026-06-28]]): this is the
        # ORIGINAL #80 shadow-first intent realised at the ORDER level. The edge is
        # real but OPTIMISTIC (OOS +1.69R regime-amplified; conservative ≈+0.2-0.4R)
        # on a THIN 96d single period — durability INCONCLUSIVE. So the order is
        # SUPPRESSED while the would-be P&L accrues live — zero capital at risk.
        shadow_first=True,
        # bars-EXTERNAL input: the funding rate (MarketView.altdata) refreshes
        # on its own intraday cadence independent of the 1H bar close — the
        # bar-advance dispatch gate must not suppress a re-eval (compute-
        # scheduling exemption only, see
        # StrategyMetadata.evaluates_in_progress_bar).
        evaluates_in_progress_bar=True,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if not bars:
            return None
        last = bars[-1]
        # WEEKEND-ONLY: the edge is the weekend crowded-short unwind. A weekday
        # bar NEVER fires.
        if not _is_weekend_utc(last.ts):
            return None
        funding = market_view.altdata.funding_rate_symbol
        p10 = market_view.altdata.funding_rate_p10
        if not is_finite(funding) or not is_finite(p10):
            return None
        if funding is None or p10 is None:
            return None
        # Extreme-NEGATIVE crowded short: funding at/below its own p10 AND < 0.
        # A positive funding can never be a crowded-SHORT capitulation.
        if funding >= 0.0 or funding > p10:
            return None
        # Depth past the threshold → strength (capped). p10 is negative here;
        # ``denom`` guards a degenerate p10 == 0.
        denom = abs(p10) if p10 != 0.0 else 1.0
        depth = min(STRENGTH_DEPTH_CAP, (p10 - funding) / denom)
        strength = min(1.0, max(STRENGTH_FLOOR, depth + STRENGTH_OFFSET))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,  # tradeable SPOT symbol — never the perp.
            side="long",  # funding = signal; trade = SPOT long (long-only).
            strength=strength,
            sizing_hint=strength,
            ttl_bars=TTL_BARS,
            thesis_tag=f"weekend_funding fr={funding:.5f}<=p10={p10:.5f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={"funding": f"{funding:.6f}", "funding_p10": f"{p10:.6f}"},
        )


__all__ = [
    "PROFIT_TARGET_R",
    "WARMUP_BARS",
    "WeekendFundingCapitulationMakerStrategy",
]
