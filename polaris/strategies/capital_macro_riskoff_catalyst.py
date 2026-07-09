"""capital_macro_riskoff_catalyst — Capital CFD GOLD, macro risk-off EVENT entry.

THESIS-FIRST (BG3 parallel manual archetype track, 2026-07-10): fills the
structural EVENT gap. ``AltDataView.vix`` / ``hy_spread`` are wired
(``polaris/strategies/base.py``) but have ZERO consumers as an ENTRY
trigger — the one strategy that reads VIX at all, ``gold_riskoff_trend_
amplify``, uses it ONLY as a strictly->=1.0 sizing AMPLIFIER *after* a
Donchian-55 breakout has already fired; it never gates entry. This module is
the missing counterpart: the macro EVENT itself (equity-vol spike + credit-
stress widening, the classic cross-asset flight-to-quality signature) IS the
trigger, with no price-breakout precondition.

Deliberately NOT a rebuild of the REJECTed ``vx_squeeze_1h_crypto4`` price-
PATTERN squeeze-breakout candidate (real-DB re-validation REJECTed it: IS
Sharpe negative, the yfinance-backtest-only edge flipped on live-DB bars) --
this trigger is macro ALT-DATA (VIX + HY OAS), not a price-technical squeeze
detector, so it does not share that candidate's failure mode.

Trigger: ``altdata.vix >= vix_threshold`` AND ``altdata.hy_spread >=
hy_spread_threshold`` (BOTH legs -- a two-factor confirmation the single-leg
amplifier never required, deliberately reducing false positives) -> GOLD
long (flight-to-quality). Absent/stale alt-data (either field ``None``) is
NEUTRAL -- no emit (degrade-never-crash, matches every other altdata-reading
strategy in the roster).

PENDING P0a GATE: NOT in ``STRATEGY_REGISTRY`` -- ``dispatch_eligible=False``
is documentary only (registry membership, not this flag, is what the live
dispatcher iterates on). Registration requires the P0a evolve engine to
clear the honest-N gate on real DB bars first (no manual eyeball promotion),
THEN a fresh Claude sub-agent external review + live-firing adversarial
check (registered != dispatched).

Verified params are named Final constants (no magic numbers):
  - ``VIX_THRESHOLD = 26.0`` (moderate-elevated -- above the ~20 historical
    average ceiling ``gold_riskoff_trend_amplify.VIX_RISKOFF_LEVEL`` already
    treats as "elevated", below a full-blown >=30 crisis print;
    P0a-varyable)
  - ``HY_SPREAD_THRESHOLD = 500.0`` (bps, ICE BofA US HY OAS -- above the
    ~300-400bps normal range, a genuine credit-stress widening, short of an
    all-time-crisis extreme; P0a-varyable; see
    ``polaris.core.altdata.fred_macro`` for the bps-normalized field)
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
from polaris.strategies.gold_breakout_1h import SUPPORTED_SYMBOLS, _norm_symbol

VIX_THRESHOLD: Final[float] = 26.0
HY_SPREAD_THRESHOLD: Final[float] = 500.0

STRENGTH_FLOOR: Final[float] = 0.5
# Each leg's excess above its threshold contributes to strength, normalized
# by a design-reasoned typical-excess denominator (a further +10 VIX points /
# +200bps HY over threshold saturates its own half of the strength budget).
VIX_EXCESS_DENOM: Final[float] = 10.0
HY_EXCESS_DENOM: Final[float] = 200.0
TTL_BARS: Final[int] = 6
LEVERAGE_MAX: Final[float] = 20.0
WARMUP_BARS: Final[int] = 5  # no bar-derived indicator -- altdata-gated only


class CapitalMacroRiskoffCatalystStrategy(BaseStrategy):
    # Varyable ENTRY-trigger knobs (P0a). Class defaults == module constants
    # == frozen baseline -> behavior-0 for default instances.
    vix_threshold: float = VIX_THRESHOLD
    hy_spread_threshold: float = HY_SPREAD_THRESHOLD
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="capital_macro_riskoff_catalyst",
        timeframe="1H",
        warmup_bars=WARMUP_BARS,
        max_positions=2,
        gross_cap=0.20,
        per_symbol_cap=0.10,
        expected_holding_bars=24,
        asset_class="commodity",
        venue="capital",
        # NO reversion/range/mean_reversion substring -> TREND exit bucket
        # (let-run; a macro flight-to-quality move is a trend event, not a
        # bounded revert-to-mean).
        correlation_group_id="cfd_gold_macro_riskoff_catalyst",
        product_class="cfd",
        hold_overnight=True,
        profit_target_r=None,
        # bars-EXTERNAL input: vix/hy_spread refresh on their own cadence
        # independent of the 1H bar close (compute-scheduling exemption
        # only, see StrategyMetadata.evaluates_in_progress_bar).
        evaluates_in_progress_bar=True,
        # PENDING P0a honest-N gate; NOT registered in STRATEGY_REGISTRY (see
        # module docstring). Documentary only while unregistered.
        dispatch_eligible=False,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if _norm_symbol(market_view.symbol) not in SUPPORTED_SYMBOLS:
            return None
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if not bars:
            return None
        vix = market_view.altdata.vix
        hy = market_view.altdata.hy_spread
        if not is_finite(vix) or not is_finite(hy) or vix is None or hy is None:
            return None
        if vix < self.vix_threshold or hy < self.hy_spread_threshold:
            return None
        last = bars[-1]
        vix_excess = (vix - self.vix_threshold) / VIX_EXCESS_DENOM
        hy_excess = (hy - self.hy_spread_threshold) / HY_EXCESS_DENOM
        scored = STRENGTH_FLOOR + 0.25 * vix_excess + 0.25 * hy_excess
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"macro_riskoff vix={vix:.1f}+hy={hy:.0f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={
                "vix": f"{vix:.1f}",
                "hy_spread": f"{hy:.0f}",
                "leverage": f"{int(LEVERAGE_MAX)}",
            },
        )


__all__ = [
    "CapitalMacroRiskoffCatalystStrategy",
    "HY_SPREAD_THRESHOLD",
    "LEVERAGE_MAX",
    "STRENGTH_FLOOR",
    "TTL_BARS",
    "VIX_THRESHOLD",
    "WARMUP_BARS",
]
