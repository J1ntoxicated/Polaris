"""bar_breakout_run — OKX SPOT, daily Donchian-40 + ROC-10 breakout (let-run).

Spec source: research selection ``bar_breakout_run`` (family=bar_momentum_horizon,
re-verified NET +97.6bps after a 20bps fee, survives both OOS halves — the
deepest fee buffer of all candidates). Daily/position horizon — a complement to
``spot_donchian`` (OKX 1H Donchian-40), NOT a duplicate: different time axis.

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
The asymmetric let-winners-run EXIT (initial hard stop = entry - 2.5*ATR + a
ratcheting up-only ATR-trail + a daily-scale time backstop) is owned by the
G5/G7 gates — the production exit FSM reads the let-run schedule from this
strategy's ``StrategyMetadata`` (TREND bucket via ``correlation_group_id`` with
no reversion substring, ``hold_overnight=True`` multi-day swing,
``profit_target_r=None`` so winners run unbounded) and scales the timeout to the
``1D`` timeframe + ``expected_holding_bars``. flow_not_block: a clean trigger
ALWAYS emits — there is no defensive entry block, no size dampen here.

ENTRY (1d bar close, deterministic, per-ticker tailored on the symbol's own
history): LONG when ``close > max(high[prior 40 bars])`` (Donchian-40 prior-high
breakout) AND ``ROC_10 = close/close[-10] - 1 > 0`` (momentum confirm). No
look-ahead: the Donchian window EXCLUDES the current closing bar (``bars[-41:-1]``)
and ROC reads only closed bars. The dedup (>=3 bars / one live same-side
position) is enforced downstream by the pipeline's anti-churn cooldown
(``bar_seconds("1D")``) + ``concurrent_same_side_open`` — not re-implemented here.

Verified params are named Final constants (no magic numbers):
  - ``DONCHIAN_WINDOW = 40``
  - ``ROC_LOOKBACK = 10``
"""

from __future__ import annotations

from typing import Final

from polaris.strategies._virtual_loosen import virtual_loosen
from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)

# VIRTUAL-mode loosening (Jin 2026-07-07): 40->20-bar Donchian (~2-2.5x trigger
# rate) + ROC-10->5 (still a positive-momentum confirm, shorter). Deepened
# 20->10 (Jin 2026-07-08): still a real 10-bar prior-high break, floor>=10.
# REAL is byte-identical (env unset -> real wins).
DONCHIAN_WINDOW: Final[int] = virtual_loosen(10, 40)
ROC_LOOKBACK: Final[int] = virtual_loosen(5, 10)

# Strength curve (frozen v1). Strength scales with the raw ROC-10 momentum,
# floored so a bare breakout still sizes meaningfully and capped at 1.0.
STRENGTH_FLOOR: Final[float] = 0.5
ROC_STRENGTH_GAIN: Final[float] = 4.0
# ttl_bars (daily): a fresh breakout stays actionable for a few days while the
# AI gates watch it. Inert-in-replay (the FSM owns the live hold), kept as a
# class default for behavior-0.
TTL_BARS: Final[int] = 3


class BarBreakoutRunStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="bar_breakout_run",
        timeframe="1D",
        warmup_bars=DONCHIAN_WINDOW + ROC_LOOKBACK + 1,
        max_positions=3,
        gross_cap=0.20,
        per_symbol_cap=0.07,
        # Daily/position horizon: ~1 month of let-run before the time backstop.
        # The G7 FSM scales the loser-timeout to ``1D`` × this count.
        expected_holding_bars=20,
        asset_class="crypto",
        venue="okx",
        # No reversion substring → TREND exit archetype (let-winners-run). This
        # is what wires the spec's ratcheting ATR-trail let-run exit in G7.
        correlation_group_id="bar_momentum_breakout",
        product_class="spot",
        # Multi-day swing — KEEP positions overnight (daily thesis is not a
        # scalp; do not EOD-flatten). OKX crypto is 24/7 EOD-exempt regardless,
        # but the flag is explicit so the role is unambiguous.
        hold_overnight=True,
        # None = keep the trend exit (ATR-trail + MFE harvest, no fixed target):
        # winners run unbounded. NOT a take-profit (this is not a revert-to-mean).
        profit_target_r=None,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < DONCHIAN_WINDOW + ROC_LOOKBACK + 1:
            return None
        last = bars[-1]

        # Donchian-40 prior-high: EXCLUDE the current closing bar (no look-ahead).
        # Reuse the pre-fed indicator when finite AND DONCHIAN_WINDOW is still 40
        # (REAL mode only — the pre-fed field is a fixed 40-bar Donchian high, not
        # valid as a stand-in once VIRTUAL mode loosens the window to 20), else
        # recompute in-module at the (possibly loosened) window.
        if DONCHIAN_WINDOW == 40 and is_finite(market_view.donchian_high_40):
            prior_high = market_view.donchian_high_40
        else:
            prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
        if prior_high is None or last.close <= prior_high:
            return None

        # ROC-10 momentum confirm (reads only closed bars — no look-ahead).
        base_close = bars[-(ROC_LOOKBACK + 1)].close
        if base_close <= 0.0:
            return None
        roc_10 = last.close / base_close - 1.0
        if roc_10 <= 0.0:
            return None

        # Strength scales with the breakout's momentum (let-winners-run sizing
        # hint), floored + capped. EXPECTANCY-positive size, never a dampen.
        scored = STRENGTH_FLOOR + ROC_STRENGTH_GAIN * roc_10
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"donchian_40_breakout+roc_10={roc_10:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "donchian_high_40": f"{prior_high:.4f}",
                "roc_10": f"{roc_10:.4f}",
            },
        )


__all__ = [
    "DONCHIAN_WINDOW",
    "ROC_LOOKBACK",
    "ROC_STRENGTH_GAIN",
    "STRENGTH_FLOOR",
    "TTL_BARS",
    "BarBreakoutRunStrategy",
]
