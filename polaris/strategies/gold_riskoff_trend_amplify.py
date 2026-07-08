"""gold_riskoff_trend_amplify — Capital CFD GOLD, D-55 trend + risk-off AMPLIFIER.

Spec source: research selection ``gold_riskoff_trend_amplify`` (rank-2, DEMO/PAPER,
NET +70bps verified). The canonical GOLD-1D-trend strategy: the same Donchian-55
breakout entry as ``gold_trend_chandelier_1d`` PLUS a continuous, strictly-positive
risk-off sizing AMPLIFIER (1.0→1.5×) — when the dollar is on a down-leg OR realized
vol is elevated, the already-long gold-trend gets sized UP. The amplifier NEVER
gates / blocks / waits for a vol-spike to enter and is NEVER < 1.0 (no defensive
dampen). The naive vol-spike crisis ENTRY gate was FALSIFIED (-1.19% to -1.95% net
— gating destroys the edge), so there is NO regime/vol gate on entry.

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
The verified asymmetric let-winners-run EXIT (initial hard stop entry-2.0*ATR(14) +
a 3.0*ATR(14) chandelier let-run trail off the running peak, re-armed only after
+0.5R MFE — NEVER tightened by the risk-off amplifier) is owned by the G5/G7 gates
via ``StrategyMetadata`` (TREND bucket — ``correlation_group_id`` has no reversion
substring → let-winners-run; ``hold_overnight=True``; ``profit_target_r=None``;
``expected_holding_bars=15``). The -1.0R rail + amplifier-never-block are
load-bearing for capturing the rare large trends. flow_not_block.

ENTRY (1d bar close, LONG-only): LONG when ``close > max(high[prior 55 bars])``
(Donchian-55 prior-high breakout; window EXCLUDES the current closing bar =
``bars[-56:-1]``, no look-ahead). Donchian-55 is NOT pre-fed → recompute in-module.

RISK-OFF AMPLIFIER (continuous, strictly >= 1.0, a sizing scalar — NEVER a gate):
  * DXY < its 20-day EMA (dollar down-leg) → amplify. DXY (DX-Y.NYB) is NOT wired
    into ``MarketView.extra`` by ``build_real_market_view`` (the dispatcher feeds
    no peer/macro closes there), so the DXY leg degrades to NEUTRAL (no amplify,
    scalar contribution 1.0) — byte-identical no-op when DXY is absent
    (degrade-never-crash). When a future additive feed wires ``extra['dxy_ema']``
    the leg activates without touching any other strategy's view.
  * realized vol elevated (``market_view.altdata.vix`` high) → amplify. VIX IS a
    populated AltDataView field; ``None`` = NEUTRAL (no amplify). A high VIX adds a
    bounded amplify increment.
The amplifier multiplies the breakout strength up to RISKOFF_AMP_MAX and is then
clamped to [STRENGTH_FLOOR, 1.0] — EXPECTANCY size, never a dampen below the base.

Verified params are named Final constants (no magic numbers):
  - ``DONCHIAN_WINDOW = 55``
  - ``ATR_TRAIL_MULT = 3.0`` / ``ATR_STOP_MULT = 2.0`` / ``ATR_LOOKBACK = 14``
    (G7-consumed exit basis)
  - ``RISKOFF_AMP_MAX = 1.5`` / ``DXY_EMA = 20`` / ``VIX_RISKOFF_LEVEL = 20.0``
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

# VIRTUAL-mode loosening (Jin 2026-07-07): 55->30-bar (~2x trigger rate); the
# risk-off amplifier only sizes, never gates — entry loosens via lookback only.
# Deepened 30->15 (Jin 2026-07-08): still a real 15-bar (1D) prior-high break,
# floor>=15. REAL byte-identical (env unset).
DONCHIAN_WINDOW: Final[int] = virtual_loosen(15, 55)
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
ATR_TRAIL_MULT: Final[float] = 3.0
ATR_STOP_MULT: Final[float] = 2.0
ATR_LOOKBACK: Final[int] = 14

# Risk-off amplifier (continuous, strictly >= 1.0 — NEVER a gate / dampen).
RISKOFF_AMP_MAX: Final[float] = 1.5
DXY_EMA: Final[int] = 20
# VIX level at/above which the realized-vol leg adds its amplify increment.
VIX_RISKOFF_LEVEL: Final[float] = 20.0
# Each active risk-off leg contributes this much above the 1.0 neutral base
# (two legs → 1.5 cap; both-absent → 1.0 neutral). NEVER below 1.0.
RISKOFF_LEG_INCREMENT: Final[float] = 0.25

# Strength curve (frozen v1).
STRENGTH_FLOOR: Final[float] = 0.5
BREAKOUT_STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3
LEVERAGE_MAX: Final[float] = 20.0

# 'GOLD' is the LIVE Capital commodity epic; 'XAUUSD' is the legacy alias. GC=F is
# the Yahoo fetch detail only — NEVER the RawSignal.symbol. Flow-safe symbol SET.
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset({"GOLD", "XAUUSD"})


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace(".", "")


def _riskoff_scalar(market_view: MarketView) -> float:
    """Continuous risk-off amplifier in [1.0, RISKOFF_AMP_MAX] — strictly >= 1.0.

    Each present risk-off condition adds ``RISKOFF_LEG_INCREMENT`` above the 1.0
    neutral base, capped at ``RISKOFF_AMP_MAX``. Absent / neutral inputs leave the
    scalar at 1.0 (degrade-never-crash, byte-identical no-op) — the amplifier is
    NEVER below 1.0 (no defensive dampen). Pure / side-effect-free.
    """
    scalar = 1.0
    # DXY down-leg: DXY (DX-Y.NYB) + its 20-day EMA are NOT wired into
    # MarketView.extra by build_real_market_view → NEUTRAL no-op here. A future
    # additive feed populating extra['dxy'] + extra['dxy_ema'] activates this leg.
    dxy = market_view.extra.get("dxy")
    dxy_ema = market_view.extra.get("dxy_ema")
    if (
        isinstance(dxy, (int, float))
        and isinstance(dxy_ema, (int, float))
        and is_finite(float(dxy))
        and is_finite(float(dxy_ema))
        and float(dxy) < float(dxy_ema)
    ):
        scalar += RISKOFF_LEG_INCREMENT
    # Elevated realized vol via VIX (a populated AltDataView field; None=neutral).
    vix = market_view.altdata.vix
    if is_finite(vix) and vix is not None and vix >= VIX_RISKOFF_LEVEL:
        scalar += RISKOFF_LEG_INCREMENT
    return min(RISKOFF_AMP_MAX, max(1.0, scalar))


class GoldRiskoffTrendAmplifyStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="gold_riskoff_trend_amplify",
        timeframe="1D",
        warmup_bars=DONCHIAN_WINDOW + 5,  # 60
        max_positions=2,
        gross_cap=0.20,
        per_symbol_cap=0.10,
        expected_holding_bars=15,
        asset_class="commodity",
        venue="capital",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="cfd_gold_riskoff_trend",
        product_class="cfd",
        hold_overnight=True,
        profit_target_r=None,
        # bars-EXTERNAL input: _riskoff_scalar reads MarketView.altdata.vix,
        # which refreshes on its own intraday cadence independent of the 1D
        # bar close — the bar-advance dispatch gate must not suppress a
        # re-eval (compute-scheduling exemption only, see
        # StrategyMetadata.evaluates_in_progress_bar).
        evaluates_in_progress_bar=True,
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
        prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
        if prior_high <= 0.0 or last.close <= prior_high:
            return None

        # Base breakout strength, then the strictly-positive risk-off amplifier.
        breakout_frac = last.close / prior_high - 1.0
        base = STRENGTH_FLOOR + BREAKOUT_STRENGTH_GAIN * breakout_frac
        risk_off = _riskoff_scalar(market_view)
        strength = min(1.0, max(STRENGTH_FLOOR, base * risk_off))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"gold_d55_riskoff+amp={risk_off:.2f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={
                "donchian_high_55": f"{prior_high:.4f}",
                "breakout_frac": f"{breakout_frac:.4f}",
                "riskoff_amp": f"{risk_off:.2f}",
                "leverage": f"{int(LEVERAGE_MAX)}",
            },
        )


__all__ = [
    "ATR_LOOKBACK",
    "ATR_STOP_MULT",
    "ATR_TRAIL_MULT",
    "BREAKOUT_STRENGTH_GAIN",
    "DONCHIAN_WINDOW",
    "DXY_EMA",
    "GoldRiskoffTrendAmplifyStrategy",
    "LEVERAGE_MAX",
    "RISKOFF_AMP_MAX",
    "RISKOFF_LEG_INCREMENT",
    "STRENGTH_FLOOR",
    "SUPPORTED_SYMBOLS",
    "TTL_BARS",
    "VIX_RISKOFF_LEVEL",
]
