"""gold_trend_chandelier_1d — Capital CFD GOLD, daily Donchian-55 trend (let-run).

Spec source: research selection ``gold_trend_chandelier_1d`` (rank-5, DEMO/PAPER,
NET +111bps verified, both OOS halves +, slippage-robust to 20bps). The canonical
GOLD daily/position trend leg: a wide 55-bar breakout that lets the chandelier
trail ride the persistent gold up-drift. GC=F 1D 2005-2026 (5394 bars) backtest;
the GC=F Yahoo series is the internal FETCH detail only — the live RawSignal.symbol
is the Capital ``GOLD`` commodity epic (``XAUUSD`` legacy alias accepted).

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
The verified asymmetric let-winners-run EXIT (initial hard stop + a 3.0*ATR(14)
chandelier trail off the running peak, re-armed only after +0.5R MFE — NO fixed
take-profit, NO tight Donchian-low harvest) is owned by the G5/G7 gates via
``StrategyMetadata`` (TREND bucket — ``correlation_group_id`` has no reversion
substring → let-winners-run; ``hold_overnight=True`` multi-day swing;
``profit_target_r=None`` so winners run unbounded; ``expected_holding_bars=21`` ≈
the avg hold so G7 scales the loser-timeout to 1D × 21). The wide trail is the
load-bearing difference from the fee-bled ``xau_indices_trend`` tight-exit failure
mode. flow_not_block: a clean breakout ALWAYS emits — no defensive entry block, no
size dampen here.

ENTRY (1d bar close, deterministic, LONG-only — symmetric short is OFF, it dilutes
gross 137→29bps and flips OOS1 negative): LONG when ``close > max(high[prior 55
bars])`` (Donchian-55 prior-high breakout; window EXCLUDES the current closing bar
= ``bars[-56:-1]``, no look-ahead). NO vol/regime gate (tested and HURTS — it
filters out the persistent-uptrend breakouts that carry). Donchian-55 is NOT
pre-fed (only Donchian 40 & 30 are) so the prior-high is recomputed in-module from
``market_view.bars`` with the ``okx_donchian_55_breakout`` is_finite() fallback
pattern. Phase2 fan-out (wajecs9ct): the GOLD anchor was the validation seed, but
the D-55 chandelier let-run archetype GENERALIZES across persistent-trend physical
commodities (two independent OOS runs + both-calendar-half). ``SUPPORTED_SYMBOLS``
is widened to the both-OOS-confirmed metals + select-energy set (see below);
🚫 the OVERFIT energy/grain (WTI/NATGAS/HEATINGOIL/WHEAT — OOS sign-flip) stay OUT.

Verified params are named Final constants (no magic numbers):
  - ``DONCHIAN_WINDOW = 55``
  - ``ATR_TRAIL_MULT = 3.0`` / ``ATR_LOOKBACK = 14`` (G7-consumed exit basis)
"""

from __future__ import annotations

from typing import Final

from polaris.strategies._virtual_loosen import virtual_loosen
from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    make_signal_id,
)

# VIRTUAL-mode loosening (Jin 2026-07-07): 55->30-bar (~2x trigger rate); daily
# bars are scarcer than 1H so keep >=30 (don't over-fire). Deepened 30->15
# (Jin 2026-07-08): still a real 15-bar (1D) prior-high break, floor>=15 for
# the daily timeframe. REAL byte-identical.
DONCHIAN_WINDOW: Final[int] = virtual_loosen(15, 55)
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
ATR_TRAIL_MULT: Final[float] = 3.0
ATR_LOOKBACK: Final[int] = 14

# Strength curve (frozen v1). Strength scales with the breakout momentum (how far
# the close clears the prior-55 high), floored so a bare breakout still sizes
# meaningfully and capped at 1.0. EXPECTANCY-positive size, never a dampen.
STRENGTH_FLOOR: Final[float] = 0.5
BREAKOUT_STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3
LEVERAGE_MAX: Final[float] = 20.0

# Live Capital commodity epics. Phase2 fan-out (wajecs9ct): the D-55 chandelier
# let-run archetype GENERALIZES across persistent-trend physical commodities — the
# strongest GENERALIZES verdict in the whole sweep (two independent OOS runs +
# both-calendar-half). Tier-1 = the 6 both-OOS-confirmed precious+base metals +
# select energy; Tier-2 = admit-with-learner-watch (one run both-OOS+ / marginal
# 2nd). Per-instrument learner tunes trail/stop, NOT entry — adding instruments
# spawns new per-ticker L4 cells / L5 NIG posteriors automatically (no new code).
# 🚨 EPIC SPELLING = the live Capital bear epic (verified vs the universe table):
# OIL_BRENT (not "BRENT"), SOYBEANOIL (not "SOYOIL"); the Yahoo SI=F/HG=F/BZ=F
# series are internal FETCH details only — NEVER the RawSignal.symbol. 'XAUUSD'
# legacy alias kept additive. 🚫 HARD EXCLUDE (OVERFIT — OOS sign-flip + fat-tail):
# WTI_CRUDE, NATGAS, HEATINGOIL, WHEAT — do NOT add. Flow-safe symbol SET.
_COMMODITY_TIER1: Final[frozenset[str]] = frozenset(
    {"GOLD", "SILVER", "PALLADIUM", "COPPER", "OIL_BRENT", "GASOLINE"}
)
_COMMODITY_TIER2_LEARNER_WATCH: Final[frozenset[str]] = frozenset(
    {"PLATINUM", "SOYBEAN", "ALUMINUM", "SOYBEANOIL"}
)
SUPPORTED_SYMBOLS: Final[frozenset[str]] = (
    _COMMODITY_TIER1 | _COMMODITY_TIER2_LEARNER_WATCH | frozenset({"XAUUSD"})
)


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace(".", "")


class GoldTrendChandelier1DStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="gold_trend_chandelier_1d",
        timeframe="1D",
        warmup_bars=DONCHIAN_WINDOW + 5,  # 60
        max_positions=2,
        gross_cap=0.20,
        per_symbol_cap=0.10,
        # avg hold 21 bars = genuinely letting winners flow; G7 scales the
        # loser-timeout to 1D × this count.
        expected_holding_bars=21,
        asset_class="commodity",
        venue="capital",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="cfd_gold_trend_chandelier",
        product_class="cfd",
        hold_overnight=True,
        profit_target_r=None,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if _norm_symbol(market_view.symbol) not in SUPPORTED_SYMBOLS:
            return None
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < DONCHIAN_WINDOW + 1:
            return None
        last = bars[-1]

        # Donchian-55 prior-high: EXCLUDE the current closing bar (no look-ahead).
        # D-55 is NOT pre-fed — recompute from bars (is_finite fallback pattern).
        prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
        if prior_high <= 0.0 or last.close <= prior_high:
            return None

        # Strength scales with how decisively the close clears the prior-55 high.
        breakout_frac = last.close / prior_high - 1.0
        scored = STRENGTH_FLOOR + BREAKOUT_STRENGTH_GAIN * breakout_frac
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"gold_donchian_55+brk={breakout_frac:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={
                "donchian_high_55": f"{prior_high:.4f}",
                "breakout_frac": f"{breakout_frac:.4f}",
                "leverage": f"{int(LEVERAGE_MAX)}",
            },
        )


__all__ = [
    "ATR_LOOKBACK",
    "ATR_TRAIL_MULT",
    "BREAKOUT_STRENGTH_GAIN",
    "DONCHIAN_WINDOW",
    "GoldTrendChandelier1DStrategy",
    "LEVERAGE_MAX",
    "STRENGTH_FLOOR",
    "SUPPORTED_SYMBOLS",
    "TTL_BARS",
]
