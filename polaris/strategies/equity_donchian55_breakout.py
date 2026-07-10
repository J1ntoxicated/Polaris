"""equity_donchian55_breakout — Alpaca US equity, daily Donchian-55 + ROC-20
breakout (let-run). Per-venue CLONE of ``okx_donchian_55_breakout`` (same
math, same trigger) — DEMO/PAPER virtual, no real capital / fees.

Spec source: ``vault`` Alpaca-sleeve build spec §1 #1 (디베이트 R2 수렴 로스터).
This is a THIRD "is the 55/20 combo itself edge-bearing" verification axis,
now on the equity feed rather than OKX crypto — the window itself
(``DONCHIAN_WINDOW=55``) is FIXED, not ``virtual_loosen``'d, because 55/20 is
the thing under test (unlike the OKX original, whose window loosens in
VIRTUAL to fire more often).

Signaling-strategy contract (ADR-008): entry trigger ONLY. The verified
asymmetric exit (ATR(20)*2.0 initial stop + Donchian-20 prior-low trail) is
G7-owned (documented here as ``ATR_STOP_MULT`` / ``TRAIL_DONCHIAN``, not
applied). ``correlation_group_id`` carries no reversion substring → TREND
let-run bucket; ``hold_overnight=True`` (multi-day swing); ``profit_target_r
=None`` (winners run unbounded); ``expected_holding_bars=25``.
flow_not_block: a clean trigger ALWAYS emits.

ENTRY (1d bar close, deterministic, per-ticker, no look-ahead): ``equity_
liquidity_ok(bars, 1)`` (T1+T2 value-based liquidity floor — §0d, no ticker
hardcode) AND ``close > max(high[bars[-56:-1]])`` (Donchian-55 prior-high,
window EXCLUDES the current bar) AND ``ROC_20 = close/close[-21] - 1 > 0``
(reuses the pre-fed ``market_view.momentum_20bar`` when finite, else
recomputes — ``is_finite`` fallback pattern, identical to the OKX original).

**Shadow grid** (measurement only, NO gating — §1): also computes whether a
narrower (40-bar) or wider (80-bar) Donchian window would ALSO have fired on
this same bar, stamped as ``tags={"shadow_w40": "1"|"0", "shadow_w80":
"1"|"0"}``. Degrades to ``"0"`` (not "insufficient data") when the shadow
window's own lookback exceeds the available bar history — a fresh symbol
(76-80 bars) reads ``shadow_w80="0"`` until it accumulates 81+ bars; this is
telemetry, never an entry gate.

Verified params are named Final constants (no magic numbers):
  - ``DONCHIAN_WINDOW = 55`` (fixed) / ``ROC_LOOKBACK = 20``
  - ``ATR_STOP_MULT = 2.0`` / ``TRAIL_DONCHIAN = 20`` (G7-consumed, exit basis)
  - ``SHADOW_WINDOW_40 = 40`` / ``SHADOW_WINDOW_80 = 80`` (measurement only)
"""

from __future__ import annotations

from typing import Final

from polaris.strategies._equity_liquidity import equity_liquidity_ok
from polaris.strategies._virtual_loosen import virtual_loosen
from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)

# Fixed — NOT virtual_loosen'd. The 55/20 combo is the thing being verified on
# the equity feed (unlike the OKX original, which loosens the window for more
# VIRTUAL firing); loosening it here would defeat the verification purpose.
DONCHIAN_WINDOW: Final[int] = 55
ROC_LOOKBACK: Final[int] = 20
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
ATR_STOP_MULT: Final[float] = 2.0
TRAIL_DONCHIAN: Final[int] = 20

# Shadow-grid comparator windows (measurement only, no gating).
SHADOW_WINDOW_40: Final[int] = 40
SHADOW_WINDOW_80: Final[int] = 80

# Strength curve (frozen v1, identical shape to the OKX original).
STRENGTH_FLOOR: Final[float] = 0.5
ROC_STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3

# §0d value-based liquidity floor (1D -> 1 bar/session).
EQ_LIQ_BARS_PER_DAY: Final[int] = 1


class EquityDonchian55BreakoutStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="equity_donchian55_breakout",
        timeframe="1D",
        warmup_bars=DONCHIAN_WINDOW + ROC_LOOKBACK + 1,  # 55 + 20 + 1 = 76
        max_positions=8,
        gross_cap=0.24,
        per_symbol_cap=0.05,
        expected_holding_bars=25,
        asset_class="equity",
        venue="alpaca",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="equity_donchian55_trend",
        product_class="equity",
        hold_overnight=True,
        profit_target_r=None,
        # VIRTUAL-only (Jin's "그만 묶어" bias): no real capital/fees in virtual,
        # so this dispatches there. REAL byte-identical (env unset -> off).
        dispatch_eligible=virtual_loosen(True, False),
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < DONCHIAN_WINDOW + ROC_LOOKBACK + 1:
            return None
        if not equity_liquidity_ok(bars, EQ_LIQ_BARS_PER_DAY):
            return None
        last = bars[-1]

        # Donchian-55 prior-high: EXCLUDE the current closing bar (no look-ahead).
        prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
        if last.close <= prior_high:
            return None

        # ROC-20 momentum confirm. Reuse the pre-fed 20-bar momentum when finite,
        # else recompute from closed bars (no look-ahead).
        if is_finite(market_view.momentum_20bar):
            roc_20 = market_view.momentum_20bar
            assert roc_20 is not None  # is_finite implies not None
        else:
            base_close = bars[-(ROC_LOOKBACK + 1)].close
            if base_close <= 0.0:
                return None
            roc_20 = last.close / base_close - 1.0
        if roc_20 <= 0.0:
            return None

        scored = STRENGTH_FLOOR + ROC_STRENGTH_GAIN * roc_20
        strength = min(1.0, max(STRENGTH_FLOOR, scored))

        # Shadow grid (measurement only): would a narrower/wider Donchian
        # window have ALSO triggered on this same bar? "0" when the shadow
        # window's own lookback exceeds the available history (fresh symbol).
        shadow_w40 = "0"
        if len(bars) >= SHADOW_WINDOW_40 + 1:
            prior_high_40 = max(b.high for b in bars[-(SHADOW_WINDOW_40 + 1):-1])
            shadow_w40 = "1" if last.close > prior_high_40 else "0"
        shadow_w80 = "0"
        if len(bars) >= SHADOW_WINDOW_80 + 1:
            prior_high_80 = max(b.high for b in bars[-(SHADOW_WINDOW_80 + 1):-1])
            shadow_w80 = "1" if last.close > prior_high_80 else "0"

        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"equity_donchian_55_breakout+roc_20={roc_20:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "donchian_high_55": f"{prior_high:.4f}",
                "roc_20": f"{roc_20:.4f}",
                "shadow_w40": shadow_w40,
                "shadow_w80": shadow_w80,
            },
        )


__all__ = [
    "ATR_STOP_MULT",
    "DONCHIAN_WINDOW",
    "EQ_LIQ_BARS_PER_DAY",
    "EquityDonchian55BreakoutStrategy",
    "ROC_LOOKBACK",
    "ROC_STRENGTH_GAIN",
    "SHADOW_WINDOW_40",
    "SHADOW_WINDOW_80",
    "STRENGTH_FLOOR",
    "TRAIL_DONCHIAN",
    "TTL_BARS",
]
