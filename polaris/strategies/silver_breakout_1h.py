"""silver_breakout_1h — Capital CFD SILVER, 1H Donchian-55 breakout (per-symbol clone).

Spec source: undefined result.passed[0].spec (Opus CONFIRMED backtest) — a
per-symbol clone of ``gold_breakout_1h`` (ADR-008 signal-generator-only): SAME
DONCHIAN_WINDOW=55 / EXIT_DONCHIAN=20 (G7-owned trail) / TTL_BARS=3 / warmup 60 /
LONG-only / venue=capital,cfd / hold_overnight=True / profit_target_r=None /
TREND let-run structure, applied to the SILVER commodity epic. per_ticker_tailored:
a SEPARATE correlation_group_id (own clone, no cross-symbol logic reuse) — SILVER
is its OWN cell, not folded into gold's group.

SILVER-GOLD co-movement is NOT a new dampener: the existing ``cfd:XAU+INDICES``
symbol-cluster cap (50%) already keys on ``asset_class`` (metal/commodity/indices
membership — ``polaris/core/sizing/cluster_cap.py``), so SILVER's commodity class
is automatically swept into the SAME cluster ceiling as GOLD/indices. No new
gate here (flow_not_block / no_defensive_param_dampen).

Session: SILVER is a GOLD-type global commodity — no discrete regional cash
session (``_session_map.session_group`` has no metals entry), so the SSOT
dispatch gate (``entry_fanout_active``) treats it as always-active on weekdays
(unmapped-symbol default), mirroring GOLD.
"""

from __future__ import annotations

from typing import Final

from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    make_signal_id,
)

DONCHIAN_WINDOW: Final[int] = 55
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
EXIT_DONCHIAN: Final[int] = 20

# Strength curve (frozen v1, byte-identical to gold_breakout_1h).
STRENGTH_FLOOR: Final[float] = 0.5
BREAKOUT_STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3
LEVERAGE_MAX: Final[float] = 20.0

# 'SILVER' is the LIVE Capital commodity epic; 'XAGUSD' is the common alias.
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset({"SILVER", "XAGUSD"})


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace(".", "")


class SilverBreakout1HStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="silver_breakout_1h",
        timeframe="1H",
        warmup_bars=DONCHIAN_WINDOW + 5,  # 60
        max_positions=2,
        gross_cap=0.20,
        per_symbol_cap=0.10,
        # ~2 trading days at 1H = let the swing flow through intraday noise.
        expected_holding_bars=48,
        asset_class="commodity",
        venue="capital",
        # No reversion substring → TREND exit archetype (let-winners-run). Its OWN
        # group id (per_ticker_tailored) — never shared with gold_breakout_1h.
        correlation_group_id="cfd_silver_breakout_1h",
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
        # look-ahead). D-55 is NOT pre-fed → recompute from bars.
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
            thesis_tag=f"silver_1h_donchian_55+brk={breakout_frac:.4f}",
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
    "LEVERAGE_MAX",
    "STRENGTH_FLOOR",
    "SUPPORTED_SYMBOLS",
    "SilverBreakout1HStrategy",
    "TTL_BARS",
]
