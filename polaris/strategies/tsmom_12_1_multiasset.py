"""tsmom_12_1_multiasset — 12-1 time-series momentum, monthly-rebalance (let-run).

Spec source: research selection ``tsmom_12_1_multiasset`` (rank-2, DEMO/PAPER).
The SLOW, low-correlation POSITION layer the multi-horizon mandate (task#15)
requires — orthogonal to the fast intraday breakout family, a designed crisis-
alpha diversifier. REPLACES the KILLed ``equity_tsmom`` (which was gross-NEGATIVE
before fees, cross-symbol): this is the honest risk-parity re-build with crypto as
the edge body. Covers the Alpaca equity 'trend/momentum' gap on the diversifier
sleeve (blocked on SIP key #42); the crypto core (OKX BTC/ETH) is live now.

expected_edge_bps = 5654 = the NO-CRYPTO-EXCLUDED honest @15bps floor (crypto-
included is far higher but crypto-concentrated — the conservative diversified
floor is used here).

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
This is a PURE REBALANCE-DRIVEN position layer — there is NO discrete stop
(vol-targeting bounds the risk). Each monthly bar re-derives the sign + vol-target;
the strategy simply stops emitting (or emits the opposite) when the 12-1 sign
flips, and G7 closes/rescales. EXIT is owned by G5/G7 via ``StrategyMetadata``
(TREND let-run — ``correlation_group_id`` has no reversion substring;
``hold_overnight=True`` position layer; ``profit_target_r=None``;
``expected_holding_bars=21`` ≈ 1 month roll). flow_not_block: a sign-positive
instrument ALWAYS emits on its rebalance bar — no defensive block, no size dampen.

ENTRY (1d bar close, evaluated per instrument independently): compute the 12-1
momentum = the trailing ~252-bar return SKIPPING the most recent ~21 bars
(``close[-(MOM_SKIP+1)] / close[-(MOM_LOOKBACK+1)] - 1`` — avoids the short-term
reversal). If 12-1 > 0 emit LONG (crypto OKX = long-only per the demo's
short-unavailable; equity/ETF Alpaca long-only). 🚨 CADENCE: this is the ONLY
monthly-rebalance strategy — the bar dispatcher fires per-bar, so the emit is
gated IN-STRATEGY to the rebalance-boundary bar (the FIRST 1D bar of a new UTC
calendar month, detected from the bar timestamps), so it does NOT emit daily. The
dispatcher's anti-churn cooldown (``bar_seconds("1D")``) + ``concurrent_same_side_
open`` hold the 1-emit/month/symbol contract downstream. 🚨 The monthly cadence IS
the structural fee-immunity — DO NOT raise the rebalance frequency or the thin
edge dies.

SIZING HINT: ``strength``/``sizing_hint`` = an inverse-realized-vol weight (63-bar
realized vol, equal-risk-contribution), normalized + capped to [0.5, 1.0]. This is
honest sum|w|≈1 fully-invested risk-parity — NO leverage inflation (the 6x-gross
run was a measurement artifact that faked Sharpe; 9-stack/leverage-inflation sizing
is FORBIDDEN by the absolute mandate).

🚨 INDICATOR GAP: the 12-1 momentum (252-21 skip) and the 63-bar realized vol are
NOT pre-fed (``momentum_20bar`` is only 20-bar) — both are recomputed in-module
from ``market_view.bars`` closes. warmup_bars = MOM_LOOKBACK + 1 = 253. The equity
sleeve is INERT until the SIP key (#42) routes equity bars (degrade-never-crash: no
bars → no emit, never a crash).

Verified params are named Final constants (no magic numbers):
  - ``MOM_LOOKBACK = 252`` / ``MOM_SKIP = 21`` / ``VOL_LOOKBACK = 63``
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Final

from polaris.strategies.base import (
    BarView,
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    make_signal_id,
)

MOM_LOOKBACK: Final[int] = 252
MOM_SKIP: Final[int] = 21
VOL_LOOKBACK: Final[int] = 63

# Inverse-vol weight: a TARGET per-asset daily vol the realized vol is scaled to.
# strength = clamp(target / realized_vol) — lower-vol assets get a higher weight
# (equal-risk-contribution), then floored + capped to [0.5, 1.0]. EXPECTANCY size,
# never a dampen / leverage inflation (honest risk-parity, sum|w|≈1).
VOL_TARGET: Final[float] = 0.02  # 2% daily realized-vol target
STRENGTH_FLOOR: Final[float] = 0.5
TTL_BARS: Final[int] = 3

# Crypto majors core (OKX, live) + a liquid equity-ETF diversifier sleeve
# (Alpaca, blocked on SIP #42). Flow-safe symbol SET — emits only here.
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset(
    {
        "BTC-USDT",
        "ETH-USDT",
        "SPY",
        "QQQ",
        "GLD",
    }
)


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "-").replace("_", "-")


def _is_rebalance_bar(bars: list[BarView]) -> bool:
    """True when the newest bar is the FIRST 1D bar of a new UTC calendar month.

    Pure + total: compares the newest bar's UTC (year, month) to the prior bar's.
    A month rollover (prev.month != last.month) marks the monthly rebalance bar,
    so the strategy emits once per month per symbol — never daily.
    """
    last_m = datetime.fromtimestamp(bars[-1].ts, tz=UTC)
    prev_m = datetime.fromtimestamp(bars[-2].ts, tz=UTC)
    return (last_m.year, last_m.month) != (prev_m.year, prev_m.month)


def _realized_vol(closes: list[float], window: int) -> float:
    """Std-dev of the trailing ``window`` daily simple returns (pure, total)."""
    rets: list[float] = []
    for i in range(len(closes) - window, len(closes)):
        prev = closes[i - 1]
        if prev <= 0.0:
            continue
        rets.append(closes[i] / prev - 1.0)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


class TSMom12_1MultiAssetStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="tsmom_12_1_multiasset",
        timeframe="1D",
        warmup_bars=MOM_LOOKBACK + 1,  # 253 (12-month lookback + the skip anchor)
        max_positions=3,
        gross_cap=0.20,
        per_symbol_cap=0.07,
        expected_holding_bars=21,  # ≈ 1 month roll
        asset_class="crypto",
        venue="okx",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="tsmom_position_momentum",
        product_class="spot",
        hold_overnight=True,
        profit_target_r=None,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if _norm_symbol(market_view.symbol) not in SUPPORTED_SYMBOLS:
            return None
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < MOM_LOOKBACK + 1:
            return None
        # 🚨 CADENCE: emit ONLY on the monthly rebalance-boundary bar.
        if not _is_rebalance_bar(bars):
            return None
        last = bars[-1]
        closes = [b.close for b in bars]

        # 12-1 momentum: trailing ~252-bar return SKIPPING the most recent ~21
        # bars (avoids short-term reversal). No look-ahead (reads only closes).
        anchor_close = closes[-(MOM_SKIP + 1)]
        base_close = closes[-(MOM_LOOKBACK + 1)]
        if base_close <= 0.0:
            return None
        mom_12_1 = anchor_close / base_close - 1.0
        # Sign-positive → LONG (long-only crypto demo + equity long). Sign-flip =
        # the strategy stops emitting and G7 closes (no opposite short built).
        if mom_12_1 <= 0.0:
            return None

        # Inverse-realized-vol weight (63-bar), normalized to the vol target and
        # floored + capped. Honest risk-parity sizing hint — NO leverage inflation.
        realized_vol = _realized_vol(closes, VOL_LOOKBACK)
        if realized_vol <= 0.0:
            strength = STRENGTH_FLOOR
        else:
            strength = min(1.0, max(STRENGTH_FLOOR, VOL_TARGET / realized_vol))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"tsmom_12_1+mom={mom_12_1:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "mom_12_1": f"{mom_12_1:.4f}",
                "realized_vol_63": f"{realized_vol:.6f}",
                "rebalance": "monthly",
            },
        )


__all__ = [
    "MOM_LOOKBACK",
    "MOM_SKIP",
    "STRENGTH_FLOOR",
    "SUPPORTED_SYMBOLS",
    "TSMom12_1MultiAssetStrategy",
    "TTL_BARS",
    "VOL_LOOKBACK",
    "VOL_TARGET",
]
