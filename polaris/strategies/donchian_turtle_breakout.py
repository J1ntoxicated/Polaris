"""donchian_turtle_breakout — canonical dual-channel Turtle (1D, let-run).

Spec source: research selection ``donchian_turtle_breakout`` (rank-4, DEMO/PAPER).
The canonical Turtle: System-1 fast 20-bar entry + System-2 slow 55-bar
confirmation (10-/20-bar channel exits owned by G7). Distinct from
``okx_donchian_55_breakout`` (single-channel D-55 + ROC) by the FASTER D-20
System-1 entry + the dual-system structure, and explicitly MULTI-ASSET (crypto
majors + a liquid equity-ETF diversifier basket) where the others are crypto-only.

🚨 STRONG robustness warning: the headline +734bps is OUTLIER-DRIVEN — the MEDIAN
trade is -116bps (57% of trades are a fee-loss), top-10 removal collapses the mean
734 → 239, and the trimmed-mean (±5%) is +101bps (86% collapse). The edge is
concentrated in a few crypto megatrends (SOL/ETH/BTC single trades) whose OOS
magnitude shrinks as crypto matures; equity ETFs are thin (+73~130bps/30yr) but
consistently positive. expected_edge_bps = 101 = the trimmed-mean floor (NOT the
+734 headline) — the conservative go-live expectancy. THEREFORE multi-symbol
diversification is MANDATORY (no crypto-solo) and exit-capture is critical: the
fat-tail (top-5 trades ≈ 45% of profit) lives or dies on the G7 trail NOT
peak-reverting winners (task#18/19/47). Ranked 4 (below the D-55 single-channel)
because its edge is the MOST outlier-dependent of the four.

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
The verified Turtle exit (entry - 2N (2*ATR) initial stop + ``close < Donchian-10
prior-low`` for System-1 / ``close < Donchian-20 prior-low`` for System-2 trend
exit, NO profit cap) is owned by G5/G7 via ``StrategyMetadata`` (TREND let-run —
``correlation_group_id`` has no reversion substring; ``hold_overnight=True``;
``profit_target_r=None``; ``expected_holding_bars=20``). flow_not_block: a clean
trigger ALWAYS emits — no defensive block, no size dampen. Pyramiding is OFF
(``PYRAMID_ENABLED=False``) so the sizing chain stays flat (9-stack mandate).

ENTRY (1d bar close, deterministic, no look-ahead — every Donchian window EXCLUDES
the current closing bar): LONG-only (crypto demo + equity long). System-1 fires
when ``close > max(high[prior 20 bars])`` (= ``bars[-21:-1]``). System-2 fires when
``close > max(high[prior 55 bars])`` (= ``bars[-56:-1]``) — slower, higher
conviction (tagged ``system=2`` with a higher strength). Emit on EITHER. The symbol
set = crypto majors + the liquid ETF diversifier basket; this is a flow-safe
symbol-SET match (emits only on its set, never blocks other strategies).

🚨 INDICATOR GAP: ``build_real_market_view`` pre-feeds ONLY Donchian 40 & 30 —
Donchian-20, Donchian-10 and Donchian-55 are NOT fed, so all are recomputed
in-module from ``market_view.bars`` (D-10/D-20 exit lows are consumed downstream
by G7, not in this entry).

Verified params are named Final constants (no magic numbers):
  - ``DON_FAST_ENTRY = 20`` / ``DON_FAST_EXIT = 10`` (System-1)
  - ``DON_SLOW_ENTRY = 55`` / ``DON_SLOW_EXIT = 20`` (System-2)
  - ``ATR_STOP_MULT = 2.0`` (= 2N) / ``PYRAMID_ENABLED = False``
"""

from __future__ import annotations

from typing import Final

from polaris.strategies._okx_liquid_universe import okx_liquid_top_n
from polaris.strategies._virtual_loosen import virtual_loosen
from polaris.strategies.base import (
    BarView,
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    make_signal_id,
)

DON_FAST_ENTRY: Final[int] = 20
DON_FAST_EXIT: Final[int] = 10
DON_SLOW_ENTRY: Final[int] = 55
DON_SLOW_EXIT: Final[int] = 20
ATR_STOP_MULT: Final[float] = 2.0
PYRAMID_ENABLED: Final[bool] = False

# Strength: System-2 (slower, higher conviction) outranks System-1.
STRENGTH_SYSTEM_1: Final[float] = 0.6
STRENGTH_SYSTEM_2: Final[float] = 0.85
TTL_BARS: Final[int] = 3

# OKX-liquid top-N crypto leg (env POLARIS_STRAT_SYMBOL_TOP_N, default 60 —
# widened from the old 3-major pin so the turtle channel has real entry breadth;
# Jin 2026-06-27) + a liquid equity-ETF diversifier basket. Multi-symbol breadth
# is REQUIRED (the edge must NOT depend on crypto-solo — robustness mandate).
# The equity-ETF leg is INERT until the Alpaca SIP key (#42) routes equity bars
# to an alpaca-venue dispatch; this crypto-venue strategy degrades-never-crashes
# on any symbol it is not routed to. Flow-safe symbol SET (never a universe block).
EQUITY_ETF_LEG: Final[frozenset[str]] = frozenset({"SPY", "QQQ", "GLD"})
SUPPORTED_SYMBOLS: Final[frozenset[str]] = okx_liquid_top_n() | EQUITY_ETF_LEG


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "-").replace("_", "-")


def _prior_high(bars: list[BarView], window: int) -> float:
    """Highest high over the prior ``window`` bars, EXCLUDING the current bar."""
    return max(b.high for b in bars[-(window + 1):-1])


class DonchianTurtleBreakoutStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="donchian_turtle_breakout",
        timeframe="1D",
        warmup_bars=DON_SLOW_ENTRY + 1,  # 56 (slow channel is the deepest window)
        max_positions=3,
        gross_cap=0.20,
        per_symbol_cap=0.07,
        expected_holding_bars=20,
        asset_class="crypto",
        venue="okx",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="turtle_donchian_breakout",
        product_class="spot",
        hold_overnight=True,
        profit_target_r=None,
        # P3 promotion (2026-07-16, vault/50_research/built-not-wired-audit.md):
        # formalizes the prior ad hoc VIRTUAL-only re-admit — see
        # session_breakout.py's identical note + polaris/strategies/__init__.py.
        dispatch_eligible=virtual_loosen(True, False),
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if _norm_symbol(market_view.symbol) not in SUPPORTED_SYMBOLS:
            return None
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < DON_SLOW_ENTRY + 1:
            return None
        last = bars[-1]

        # System-2 (slow 55-bar) takes precedence: higher conviction, higher
        # strength. Both channels EXCLUDE the current closing bar (no look-ahead).
        slow_high = _prior_high(bars, DON_SLOW_ENTRY)
        fast_high = _prior_high(bars, DON_FAST_ENTRY)
        if last.close > slow_high:
            system = 2
            entry_channel = DON_SLOW_ENTRY
            prior_high = slow_high
            strength = STRENGTH_SYSTEM_2
        elif last.close > fast_high:
            system = 1
            entry_channel = DON_FAST_ENTRY
            prior_high = fast_high
            strength = STRENGTH_SYSTEM_1
        else:
            return None

        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"turtle_s{system}_donchian_{entry_channel}_breakout",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "system": str(system),
                "entry_channel": str(entry_channel),
                "prior_high": f"{prior_high:.4f}",
            },
        )


__all__ = [
    "ATR_STOP_MULT",
    "DON_FAST_ENTRY",
    "DON_FAST_EXIT",
    "DON_SLOW_ENTRY",
    "DON_SLOW_EXIT",
    "DonchianTurtleBreakoutStrategy",
    "PYRAMID_ENABLED",
    "STRENGTH_SYSTEM_1",
    "STRENGTH_SYSTEM_2",
    "SUPPORTED_SYMBOLS",
    "TTL_BARS",
]
