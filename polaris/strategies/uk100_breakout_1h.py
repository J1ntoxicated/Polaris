"""uk100_breakout_1h — Capital CFD UK100 (FTSE 100), 1H Donchian-55 breakout.

Spec source: undefined result.passed[0].spec (Opus CONFIRMED backtest) — a
per-symbol clone of ``gold_breakout_1h`` (ADR-008 signal-generator-only): SAME
DONCHIAN_WINDOW=55 / EXIT_DONCHIAN=20 (G7-owned trail) / TTL_BARS=3 / warmup 60 /
LONG-only / venue=capital,cfd / hold_overnight=True / profit_target_r=None /
TREND let-run structure, applied to the UK100 index epic. per_ticker_tailored:
its OWN correlation_group_id — never shared with the GOLD/SILVER/US100 clones.

PROBE per_symbol_cap (spec: "initial per-symbol cap reduced — a probe, IS
half-sample was net-negative"): ``PER_SYMBOL_CAP`` starts at HALF the sibling
clones' 0.10 (0.05), env-tunable via ``POLARIS_UK100_PER_SYMBOL_CAP_PCT`` so it
can be widened back without a code change once live data confirms the edge —
NOT a defensive throttle (the trigger/exit/TTL are byte-identical to the other
clones; only the PER-SYMBOL notional ceiling starts narrower for this one name).

Session gate (weekday+holiday-aware SSOT clock mandate): UK100 only fires its
NEW-ENTRY dispatch inside its own cash session window via the SSOT gate
``polaris.scripts._session_map.entry_fanout_active`` (session_group('UK100') ==
'europe', 07:00-16:00 UTC, weekday-only) — consulted at the dispatch call site,
NOT duplicated here (ADR-008: strategies are pure signal generators, no I/O /
clock reads). This module itself has no time-of-day logic; it only recomputes
the Donchian-55 trigger from bars.
"""

from __future__ import annotations

import os
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
# rate, same 1H UK100 breakout mechanism. Deepened 20->10 (Jin 2026-07-08):
# still a real 10-bar prior-high break, floor>=10. Session gate is UNCHANGED
# (weekday+holiday SSOT, not an entry-loosening knob). REAL byte-identical
# (env unset).
DONCHIAN_WINDOW: Final[int] = virtual_loosen(10, 55)
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
EXIT_DONCHIAN: Final[int] = 20

# Strength curve (frozen v1, byte-identical to gold_breakout_1h).
STRENGTH_FLOOR: Final[float] = 0.5
BREAKOUT_STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3
LEVERAGE_MAX: Final[float] = 20.0

# 'UK100' is the LIVE Capital index epic (FTSE 100).
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset({"UK100"})

# Probe cap: HALF the sibling clones' 0.10 default (IS half-sample was
# net-negative — env-tunable so a live-confirmed edge can widen it back without
# a code change; never a code-level throttle).
_PER_SYMBOL_CAP_ENV: Final[str] = "POLARIS_UK100_PER_SYMBOL_CAP_PCT"
_PER_SYMBOL_CAP_DEFAULT: Final[float] = 0.05


def _per_symbol_cap() -> float:
    raw = os.environ.get(_PER_SYMBOL_CAP_ENV)
    if raw is None or raw == "":
        return _PER_SYMBOL_CAP_DEFAULT
    try:
        return float(raw)
    except ValueError:
        return _PER_SYMBOL_CAP_DEFAULT


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace(".", "")


class UK100Breakout1HStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="uk100_breakout_1h",
        timeframe="1H",
        warmup_bars=DONCHIAN_WINDOW + 5,  # 60
        max_positions=2,
        gross_cap=0.20,
        per_symbol_cap=_per_symbol_cap(),
        # ~2 trading days at 1H = let the swing flow through intraday noise.
        expected_holding_bars=48,
        asset_class="indices",
        venue="capital",
        # No reversion substring → TREND exit archetype (let-winners-run). Its OWN
        # group id (per_ticker_tailored) — never shared with the other clones.
        correlation_group_id="cfd_uk100_breakout_1h",
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
            thesis_tag=f"uk100_1h_donchian_55+brk={breakout_frac:.4f}",
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
    "TTL_BARS",
    "UK100Breakout1HStrategy",
]
