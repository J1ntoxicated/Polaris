"""okx_donchian_55_breakout — OKX SPOT, daily Donchian-55 + ROC-20 breakout (let-run).

Spec source: research selection ``okx_donchian_55_breakout`` (rank-1, DEMO/PAPER).
Consolidates the two verified D-55 1D OKX-crypto candidates (donchian_55_20_turtle
+4149bps & okx_donchian_55_swing +789bps — near-duplicate rulesets on the same
D-55 1D axis) into one canonical strategy. A THIRD time axis distinct from
``spot_donchian`` (D-40 1H) and ``bar_breakout_run`` (D-40 1D): a wider/slower
55-bar channel, NOT a duplicate — it reinforces the OKX-crypto trend/breakout
family that is the surviving fee-beating archetype.

expected_edge_bps = 570 — the donchian_55_20 top-5%-trimmed robust floor (NOT the
+4149 outlier-driven headline): a conservative, fat-tail-independent sizing basis.
Realized capture is contingent on the G7 let-run FSM holding winners unbounded.

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
The verified asymmetric exit (initial hard stop = entry - 2.0*ATR(20) + a
``close < Donchian-20 prior-low`` trailing harvest) is owned by the G5/G7 gates
via ``StrategyMetadata`` (TREND bucket — ``correlation_group_id`` has no reversion
substring → let-winners-run; ``hold_overnight=True`` multi-day swing;
``profit_target_r=None`` so winners run unbounded; ``expected_holding_bars=25`` so
G7 scales the loser-timeout to 1D × 25 ≈ 1 month backstop). If exits clip winners
early the realized edge compresses sharply (task#18/19/47 exit-capture is the
load-bearing dependency). flow_not_block: a clean trigger ALWAYS emits — no
defensive entry block, no size dampen here.

ENTRY (1d bar close, deterministic, per-ticker on the symbol's own history):
LONG when ``close > max(high[prior 55 bars])`` (Donchian-55 prior-high breakout,
window EXCLUDES the current closing bar = ``bars[-56:-1]``, no look-ahead) AND
``ROC_20 = close/close[-21] - 1 > 0`` (uptrend-regime momentum confirm; reuses the
pre-fed ``market_view.momentum_20bar`` when finite, else recomputes). Long-only
OKX SPOT cash (lev1). The symbol gate restricts emits to large-cap liquid USDT
pairs so daily-horizon depth avoids thin-alt 51155 — applied as a flow-safe
symbol-SET match (this strategy emits only on its set; it never blocks other
strategies' emits on any symbol).

🚨 INDICATOR GAP: ``build_real_market_view`` pre-feeds ONLY Donchian 40 & 30 —
Donchian-55 is NOT fed, so the prior-high is recomputed in-module from
``market_view.bars`` with the ``bar_breakout_run`` is_finite() fallback pattern.
The D-20 trailing-low is consumed downstream by G7, not in this entry.

Verified params are named Final constants (no magic numbers):
  - ``DONCHIAN_WINDOW = 55`` / ``ROC_LOOKBACK = 20``
  - ``ATR_STOP_MULT = 2.0`` / ``TRAIL_DONCHIAN = 20`` (G7-consumed, exit basis)
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

DONCHIAN_WINDOW: Final[int] = 55
ROC_LOOKBACK: Final[int] = 20
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
ATR_STOP_MULT: Final[float] = 2.0
TRAIL_DONCHIAN: Final[int] = 20

# Strength curve (frozen v1). Strength scales with the raw ROC-20 momentum,
# floored so a bare breakout still sizes meaningfully and capped at 1.0.
STRENGTH_FLOOR: Final[float] = 0.5
ROC_STRENGTH_GAIN: Final[float] = 4.0
# ttl_bars (daily): a fresh breakout stays actionable for a few days while the
# AI gates watch it. Inert-in-replay (the FSM owns the live hold).
TTL_BARS: Final[int] = 3

# Large-cap liquid USDT pairs — daily-horizon depth avoids thin-alt 51155.
# Flow-safe symbol SET (this strategy emits only here; never blocks others).
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset(
    {"BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT"}
)


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "-").replace("_", "-")


class OKXDonchian55BreakoutStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="okx_donchian_55_breakout",
        timeframe="1D",
        warmup_bars=DONCHIAN_WINDOW + ROC_LOOKBACK + 1,  # 55 + 20 + 1 = 76
        max_positions=3,
        gross_cap=0.20,
        per_symbol_cap=0.07,
        # ~1 month of let-run before the G7 time backstop (1D × 25).
        expected_holding_bars=25,
        asset_class="crypto",
        venue="okx",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="okx_donchian_55_breakout",
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
        if len(bars) < DONCHIAN_WINDOW + ROC_LOOKBACK + 1:
            return None
        last = bars[-1]

        # Donchian-55 prior-high: EXCLUDE the current closing bar (no look-ahead).
        # D-55 is NOT pre-fed — recompute from bars (is_finite fallback pattern).
        prior_high = max(b.high for b in bars[-(DONCHIAN_WINDOW + 1):-1])
        if last.close <= prior_high:
            return None

        # ROC-20 momentum confirm. Reuse the pre-fed 20-bar momentum when finite
        # (== close/close[-21]-1), else recompute from closed bars (no look-ahead).
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

        # Strength scales with the breakout's momentum (let-winners-run sizing
        # hint), floored + capped. EXPECTANCY-positive size, never a dampen.
        scored = STRENGTH_FLOOR + ROC_STRENGTH_GAIN * roc_20
        strength = min(1.0, max(STRENGTH_FLOOR, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"donchian_55_breakout+roc_20={roc_20:.4f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "donchian_high_55": f"{prior_high:.4f}",
                "roc_20": f"{roc_20:.4f}",
            },
        )


__all__ = [
    "ATR_STOP_MULT",
    "DONCHIAN_WINDOW",
    "OKXDonchian55BreakoutStrategy",
    "ROC_LOOKBACK",
    "ROC_STRENGTH_GAIN",
    "STRENGTH_FLOOR",
    "SUPPORTED_SYMBOLS",
    "TRAIL_DONCHIAN",
    "TTL_BARS",
]
