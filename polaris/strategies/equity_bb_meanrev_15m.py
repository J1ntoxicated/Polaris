"""equity_bb_meanrev_15m — Alpaca US equity, 15m Bollinger-lower mean-reversion
INSIDE an RTH-interior window. DEMO/PAPER virtual, no real capital / fees.

Spec source: ``vault`` Alpaca-sleeve build spec §1 #4 (디베이트 R2 수렴 로스터, Wave 1b).
This is a SHAPE clone of ``rsi_bb_pullback`` (RSI<30 + BB-lower touch + trend
filter) — no R-tuned numeric value is ported (no ``virtual_loosen`` on the
entry trigger: ``RSI_THRESHOLD=30`` and the ``close <= bb_lower`` touch stay
FIXED in both VIRTUAL and REAL; only ``dispatch_eligible`` loosens, same as
every other Wave-1 Alpaca clone).

Signaling-strategy contract (ADR-008): entry trigger ONLY. ``correlation_
group_id="equity_bb_mean_reversion_15m"`` — the id MUST carry the "reversion"
substring (``exit_thesis.bucket_from_correlation_group``) so the exit
archetype resolves to ``Bucket.REVERSION`` (bounded harvest schedule) instead
of falling through to the ``Bucket.TREND`` let-winners-run default — the exact
bug class fixed one day earlier for ``cci_reversion``/``connors_rsi2``
([[exit_peak_lock_bind_2026-07-10]]). ``profit_target_r=1.0`` caps the
entry's UPSIDE but does NOT by itself restore the reversion-side health/trail
schedule — the substring match is REQUIRED, not redundant with the target.
``hold_overnight=False`` → EOD flatten is the BASE case (an intraday
mean-revert bet is not a swing hold). ``expected_holding_bars=4``.
flow_not_block: a clean trigger ALWAYS emits.

ENTRY (15m bar close, LONG-only, deterministic, no look-ahead) requires ALL:
  (1) ``equity_liquidity_ok(bars, 26)`` (T1+T2 value-based liquidity floor —
      §0d, 26 bars/session for a 6.5h RTH day of 15m bars);
  (2) ``us_equity_rth_interior(last.ts)`` (§0b — the first/last 30min of RTH
      are excluded, same integrity-class gate as the RTH entry-hold itself);
  (3) ``close > SMA200`` computed over THIS stream's own 15m closes (reuses
      ``connors_rsi2._sma`` — common-code reuse, NOT the pre-fed
      ``market_view.ma_200`` field, per the build spec);
  (4) ``close <= bb_lower`` (``market_view.bb_lower``, pre-fed — EXACT touch,
      no near-touch multiplier, unlike the OKX original's VIRTUAL-loosened
      ``BB_TOUCH_MULT``);
  (5) ``rsi_14 < 30`` (``market_view.rsi_14``, pre-fed, fixed threshold — no
      ``virtual_loosen`` on the number itself).
  Any pre-fed field being ``None`` (insufficient upstream history) → no-emit
  (degrade-never-crash, same ``is_finite`` fallback pattern as the OKX clone).

Take-profit: ``profit_target_r=1.0`` — the BB half-width is ≈2σ ≈ the position's
own initial-risk R, so a revert to the BB MIDLINE is ≈+1R (the ``connors_rsi2.
REVERT_TARGET_R`` precedent). 🚨 Do NOT port the OKX original's tuned
``+0.553R`` value here — that number belongs to the OKX RSI/BB-touch-mult
combo this module does NOT clone; ``1.0`` is this module's OWN target, not an
import.

🚨 OPS: **100-trade gross (price-only, fee-前) < 0 → unconditional KILL**
(un-register from ``dispatch_eligible`` / registry, same class as the B1-prune
survivors' kill trigger) — track this in the daily digest gross rollup for
``strategy_id="equity_bb_meanrev_15m"``.

Verified params are named Final constants (no magic numbers):
  - ``RSI_THRESHOLD = 30.0`` (fixed) / ``SMA_WINDOW = 200`` / ``WARMUP_BARS = 205``
  - ``EQ_LIQ_BARS_PER_DAY = 26`` / ``REVERT_TARGET_R = 1.0``
"""

from __future__ import annotations

from typing import Final

from polaris.core.sessions.equity_session_gate import us_equity_rth_interior
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
from polaris.strategies.connors_rsi2 import _sma

# Shape-clone of rsi_bb_pullback — NO R-tuned value ported (no virtual_loosen
# on the trigger numbers). Fixed in BOTH virtual and real.
RSI_THRESHOLD: Final[float] = 30.0
SMA_WINDOW: Final[int] = 200
WARMUP_BARS: Final[int] = 205  # SMA_WINDOW(200) + 5, connors_rsi2 warmup pattern.

# §0d value-based liquidity floor (15m -> 26 bars/session, 6.5h / 15m).
EQ_LIQ_BARS_PER_DAY: Final[int] = 26

# BB-midline harvest target — this module's OWN number (connors REVERT_TARGET_R
# precedent), NOT the OKX original's +0.553R.
REVERT_TARGET_R: Final[float] = 1.0

# Strength curve (frozen v1, connors_rsi2/rsi_bb_pullback shape).
STRENGTH_FLOOR: Final[float] = 0.4
STRENGTH_OFFSET: Final[float] = 0.5
TTL_BARS: Final[int] = 4

# §0c loss-reentry cooldown — 8 bars @ 15m = 2h symbol-local pacing.
LOSS_COOLDOWN_BARS: Final[int] = 8


class EquityBbMeanrev15mStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="equity_bb_meanrev_15m",
        timeframe="15m",
        warmup_bars=WARMUP_BARS,
        max_positions=4,
        gross_cap=0.12,
        per_symbol_cap=0.04,
        expected_holding_bars=4,
        asset_class="equity",
        venue="alpaca",
        # "reversion" substring REQUIRED — bucket_from_correlation_group()
        # routes on this exact string, not strategy_id (2026-07-11 fix).
        correlation_group_id="equity_bb_mean_reversion_15m",
        product_class="equity",
        hold_overnight=False,
        profit_target_r=REVERT_TARGET_R,
        dispatch_eligible=virtual_loosen(True, False),
        loss_cooldown_bars=LOSS_COOLDOWN_BARS,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < WARMUP_BARS:
            return None
        if not equity_liquidity_ok(bars, EQ_LIQ_BARS_PER_DAY):
            return None
        last = bars[-1]
        if not us_equity_rth_interior(last.ts):
            return None

        sma200 = _sma(bars, SMA_WINDOW)
        if sma200 is None or last.close <= sma200:
            return None

        if not is_finite(market_view.bb_lower):
            return None
        bb_lo = market_view.bb_lower
        assert bb_lo is not None  # is_finite implies not None
        if last.close > bb_lo:
            return None

        if not is_finite(market_view.rsi_14):
            return None
        rsi = market_view.rsi_14
        assert rsi is not None  # is_finite implies not None
        if rsi >= RSI_THRESHOLD:
            return None

        # Strength: deeper oversold (lower RSI(14)) → stronger.
        depth = (RSI_THRESHOLD - rsi) / RSI_THRESHOLD
        strength = min(1.0, max(STRENGTH_FLOOR, depth + STRENGTH_OFFSET))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"equity_bb_meanrev_15m+rsi={rsi:.1f}<30+bb_lo+sma200",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "rsi_14": f"{rsi:.1f}",
                "bb_lower": f"{bb_lo:.4f}",
                "sma_200": f"{sma200:.4f}",
            },
        )


__all__ = [
    "EQ_LIQ_BARS_PER_DAY",
    "EquityBbMeanrev15mStrategy",
    "LOSS_COOLDOWN_BARS",
    "REVERT_TARGET_R",
    "RSI_THRESHOLD",
    "SMA_WINDOW",
    "STRENGTH_FLOOR",
    "STRENGTH_OFFSET",
    "TTL_BARS",
    "WARMUP_BARS",
]
