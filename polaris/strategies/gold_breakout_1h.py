"""gold_breakout_1h — Capital CFD GOLD, 1H Donchian-55 breakout (Turtle-2 let-run).

Spec source: research selection ``gold_breakout_1h`` (rank-6, DEMO/PAPER, NET
+34bps verified, param-jitter robust across 9 combos +20-34bps — no cliff, not
curve-fit). The intraday/swing GOLD bin: an orthogonal TIME axis to the daily
``gold_trend_chandelier_1d`` (same GOLD epic, different bar) — NOT a duplicate.
GC=F 1H 730d backtest (13726 bars); GC=F is the Yahoo fetch detail only — the live
RawSignal.symbol is the Capital ``GOLD`` commodity epic (``XAUUSD`` legacy alias).
Adds a higher-frequency gold entry (85 vs 29 trades) WITHOUT scalp/churn.

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
The verified asymmetric let-winners-run EXIT (trail exit when ``close < 20-bar
Donchian prior-low`` — the classic Turtle-2 slow-entry / faster-exit structure,
banks on a structural trend break, holds through intraday noise) is owned by the
G5/G7 gates via ``StrategyMetadata`` (TREND bucket — ``correlation_group_id`` has
no reversion substring → let-winners-run; ``hold_overnight=True`` for the 1-3 day
swing through intraday noise; ``profit_target_r=None`` so winners run unbounded;
``expected_holding_bars=48`` ≈ 2 trading days at 1H). The asymmetric payoff
(avg_win +210bps vs avg_loss -107bps, W/L 1.96) = let-run / loss_profit_asymmetry,
NOT high-WR mean-reversion. flow_not_block.

ENTRY (1H bar close, LONG-only — gold's persistent up-drift; 1H shorts whipsaw):
LONG when ``close > max(high[prior 55 bars])`` (55-bar 1H Donchian prior-high
breakout; window EXCLUDES the current closing bar = ``bars[-56:-1]``, no
look-ahead). Donchian-55 is NOT pre-fed → recompute in-module.

Verified params are named Final constants (no magic numbers):
  - ``DONCHIAN_WINDOW = 55`` (entry channel)
  - ``EXIT_DONCHIAN = 20`` (G7-consumed exit basis — faster trail)
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

# VIRTUAL-mode loosening (Jin 2026-07-07): 55->20-bar channel, ~2.5-3x trigger
# rate, same 1H gold breakout mechanism. REAL byte-identical (env unset).
DONCHIAN_WINDOW: Final[int] = virtual_loosen(20, 55)
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
EXIT_DONCHIAN: Final[int] = 20

# Strength curve (frozen v1). Scales with the breakout magnitude, floored + capped.
STRENGTH_FLOOR: Final[float] = 0.5
BREAKOUT_STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3
LEVERAGE_MAX: Final[float] = 20.0

# 'GOLD' is the LIVE Capital commodity epic; 'XAUUSD' is the legacy alias. GC=F is
# the Yahoo fetch detail only — NEVER the RawSignal.symbol. Flow-safe symbol SET.
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset({"GOLD", "XAUUSD"})


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace(".", "")


class GoldBreakout1HStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="gold_breakout_1h",
        timeframe="1H",
        warmup_bars=DONCHIAN_WINDOW + 5,  # 60
        max_positions=2,
        gross_cap=0.20,
        per_symbol_cap=0.10,
        # ~2 trading days at 1H = let the swing flow through intraday noise.
        expected_holding_bars=48,
        asset_class="commodity",
        venue="capital",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="cfd_gold_breakout_1h",
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

        # 55-bar 1H Donchian prior-high: EXCLUDE the current closing bar (no
        # look-ahead). D-55 is NOT pre-fed — recompute from bars.
        prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
        if prior_high <= 0.0 or last.close <= prior_high:
            return None

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
            thesis_tag=f"gold_1h_donchian_55+brk={breakout_frac:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={
                "donchian_high_55": f"{prior_high:.4f}",
                "breakout_frac": f"{breakout_frac:.4f}",
                "exit_donchian_20": str(EXIT_DONCHIAN),
                "leverage": f"{int(LEVERAGE_MAX)}",
            },
        )


__all__ = [
    "BREAKOUT_STRENGTH_GAIN",
    "DONCHIAN_WINDOW",
    "EXIT_DONCHIAN",
    "GoldBreakout1HStrategy",
    "LEVERAGE_MAX",
    "STRENGTH_FLOOR",
    "SUPPORTED_SYMBOLS",
    "TTL_BARS",
]
