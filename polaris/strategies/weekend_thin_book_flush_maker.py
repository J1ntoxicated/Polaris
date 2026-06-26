"""weekend_thin_book_flush_maker — the single verified crypto maker BUILD (#77).

Spec source: the crypto maker research (task w5xhhz2m9) — of 12 maker-validated
intraday strategies, 11 stayed net-negative under the real maker fee; ONLY this
one flipped net-positive (+73 bps real-fee). Its edge is NOT the fee saving but
a microstructure premium: on a WEEKEND (Sat/Sun UTC) the OKX book is thin, and a
flush (an over-sold over-correction) lets a deep passive bid fill BELOW fair
value and harvest the revert.

Signal generator ONLY (ADR-008): emits ``RawSignal | None``. Lifecycle (entry
routing / exit / rail) is owned by the AI gate pipeline + live-recalc engine:

  * ENTRY routing — REVERSION correlation group ⇒ the bar order-mode router posts
    a passive ``post_only`` at the touch (no spread cross); ``maker_no_fill_cancel``
    ⇒ a no-fill SKIPs (a missed deep bid is 0 realised cost, never a taker).
  * EXIT — ``profit_target_r`` harvests the bounded clean revert (+~0.30R clean
    fill); the -1.0R rail is the floor and is executed by the engine as a TAKER
    (a maker stop could rest unfilled past the rail — never a maker on the rail).

Trigger (long-only, OKX SPOT): a WEEKEND bar where RSI(14) is deeply over-sold
AND the bar wicked through the lower Bollinger band (a capitulation flush). The
DEPTH of the passive bid is the execution layer's job; the strategy only flags
the flush. A weekday bar NEVER fires — the edge is the weekend thin book.
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

# A deep over-sold flush — tighter than a routine RSI-BB pullback (the weekend
# thesis wants a genuine over-correction, not a shallow dip).
RSI_FLUSH_THRESHOLD = 25.0
WARMUP_BARS = 24  # enough for RSI(14) + a Bollinger(20) lower band

# Clean-revert harvest target (R). The research asymmetry: clean fill +~0.30R /
# pick-off rides to the -1.0R rail (engine-owned taker). A bounded take-profit,
# never a let-winners-run (the edge is the bounded revert-to-mean).
REVERT_TARGET_R = 0.30

# Strength curve (deeper flush → stronger).
STRENGTH_FLOOR = 0.4
STRENGTH_OFFSET = 0.5
TTL_BARS = 3

_WEEKEND_UTC_WEEKDAYS = frozenset({5, 6})  # Sat=5, Sun=6


def _is_weekend_utc(ts: int) -> bool:
    """True when the bar's UTC weekday is Saturday or Sunday (pure, from ts)."""
    return datetime.fromtimestamp(ts, tz=UTC).weekday() in _WEEKEND_UTC_WEEKDAYS


class WeekendThinBookFlushMakerStrategy(BaseStrategy):
    metadata = StrategyMetadata(
        strategy_id="weekend_thin_book_flush_maker",
        timeframe="1H",
        warmup_bars=WARMUP_BARS,
        max_positions=6,
        gross_cap=0.18,
        per_symbol_cap=0.06,
        expected_holding_bars=6,
        asset_class="spot",
        venue="okx",
        # "mean_reversion" substring ⇒ REVERSION bucket ⇒ passive post-only entry
        # mode + a bounded revert exit (NOT let-winners-run).
        correlation_group_id="weekend_thin_book_mean_reversion",
        # bounded clean-revert harvest (the +0.30R clean-fill leg); the -1.0R
        # rail (engine-owned, TAKER) is the floor.
        profit_target_r=REVERT_TARGET_R,
        # no-fill = CANCEL/skip: the missed deep bid is 0 realised cost, the
        # edge IS the passive fill — never a forced taker fallback.
        maker_no_fill_cancel=True,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if not bars:
            return None
        last = bars[-1]
        # WEEKEND-ONLY: the edge is the weekend thin book. A weekday flush is the
        # fee-fatal intraday class the research REJECTed — never fire.
        if not _is_weekend_utc(last.ts):
            return None
        if not is_finite(market_view.rsi_14) or not is_finite(market_view.bb_lower):
            return None
        rsi = market_view.rsi_14
        bb_lo = market_view.bb_lower
        if rsi is None or rsi >= RSI_FLUSH_THRESHOLD:
            return None
        # A capitulation flush: the bar wicked THROUGH the lower band.
        if bb_lo is None or last.low > bb_lo:
            return None
        depth = (RSI_FLUSH_THRESHOLD - rsi) / RSI_FLUSH_THRESHOLD
        strength = min(1.0, max(STRENGTH_FLOOR, depth + STRENGTH_OFFSET))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=TTL_BARS,
            thesis_tag=f"weekend_flush rsi={rsi:.1f}<{RSI_FLUSH_THRESHOLD:.0f}+wick",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={"rsi": f"{rsi:.1f}", "bb_lower": f"{bb_lo:.6f}"},
        )


__all__ = [
    "REVERT_TARGET_R",
    "RSI_FLUSH_THRESHOLD",
    "WARMUP_BARS",
    "WeekendThinBookFlushMakerStrategy",
]
