"""index_dual_momentum_rotation — Capital CFD index, monthly dual-momentum rotation.

Spec source: research selection ``index_dual_momentum_rotation`` (rank-1,
DEMO/PAPER, NET +78bps verified, -45% maxDD vs -54% buy&hold). Antonacci dual
momentum on the 5-index Capital complex: on the monthly-rebalance boundary, rank
the indices by 6-month ROC_120 and go long the top-2 that also pass the
ABSOLUTE-momentum gate (ROC_120 > 0). The absolute gate IS the load-bearing
drawdown-reducing alpha (cuts bear exposure) — preserve it. The monthly cadence IS
the fee-immunity; do NOT raise it.

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
EXIT is owned by the G5/G7 gates via ``StrategyMetadata`` (TREND let-run —
``correlation_group_id`` has no reversion substring → let-winners-run;
``hold_overnight=True`` position layer; ``profit_target_r=None`` so winners run
unbounded — this is rotation, not revert-to-mean; ``expected_holding_bars=21`` ≈ 1
month roll). Documented exit schedule (G7 applies, the strategy does not): a
rebalance-driven roll — G7 closes the prior holding when the index drops out of
top-2 OR its ROC_120 turns negative (absolute-momentum exit); tail-rail stop
entry-2.5*ATR(20). flow_not_block: a passing index ALWAYS emits on its rebalance
bar; cash safe-harbor = the ABSENCE of an emit, NOT a block of other strategies.

ENTRY (1d bar close): emit ONLY on the monthly-rebalance boundary bar (the FIRST
1D bar of a new UTC calendar month, detected in-module via the
``tsmom_12_1_multiasset._is_rebalance_bar`` pattern — NOT daily). On that bar, per
symbol compute ``ROC_120 = close/close[-121] - 1`` (recomputed in-module from
``market_view.bars`` closes; NOT pre-fed). Then:
  * ABSOLUTE-momentum gate: emit LONG only if that symbol's ROC_120 > 0.
  * RELATIVE gate: emit LONG only if the symbol is in the top-2 of the 5-index
    ROC_120 ranking.
🚨 CROSS-SYMBOL: ``generate_raw_signal`` is per-symbol, but the relative rank needs
all 5 indices' ROC_120 simultaneously. ``build_real_market_view`` does NOT wire
peer-index closes into ``MarketView.extra`` (the dispatcher feeds no peer closes
there), so absent a peer feed the strategy DEGRADES to the absolute-gate-only
branch (ROC_120 > 0 → LONG) and NEVER crashes (degrade-never-crash). When a future
additive feed populates ``extra['peer_roc_120']`` (a {symbol: roc_120} map of the
universe) the relative top-2 gate activates — byte-identical no-op for every other
strategy until then.

strength/sizing_hint = a normalized rank score (rank-1 → 1.0, rank-2 → ~0.75,
floored 0.5, capped 1.0) when the peer rank is known, else the absolute-momentum
strength — EXPECTANCY size, never a dampen.

Verified params are named Final constants (no magic numbers):
  - ``ROC_LOOKBACK = 120`` / ``TOP_N = 2``
  - ``ATR_STOP_MULT = 2.5`` (G7-consumed exit basis)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from polaris.strategies._virtual_loosen import virtual_loosen
from polaris.strategies.base import (
    BarView,
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    make_signal_id,
)

# VIRTUAL-mode loosening (Jin 2026-07-08): 120->60-bar (3-month) ROC is still a
# genuine multi-month momentum measure (floor>=60), just faster to rank/rotate.
# REAL byte-identical (env unset).
ROC_LOOKBACK: Final[int] = virtual_loosen(60, 120)
# VIRTUAL-mode loosening (Jin 2026-07-07): TOP_N 2->4 admits more of the 5-index
# set per rebalance while the ABSOLUTE-momentum gate (ROC_120>0) stays intact —
# still dual-momentum, not a bare admit. Monthly cadence (the fee-immunity) is
# UNTOUCHED. REAL byte-identical (env unset).
TOP_N: Final[int] = virtual_loosen(4, 2)
# Exit basis (G7-owned — documented here as the verified schedule, not applied):
ATR_STOP_MULT: Final[float] = 2.5

# Strength curve (frozen v1).
STRENGTH_FLOOR: Final[float] = 0.5
RANK1_SCORE: Final[float] = 1.0
RANK2_SCORE: Final[float] = 0.75
ROC_STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3
LEVERAGE_MAX: Final[float] = 20.0

# Live Capital bare index epics (AU200AU alias accepted). Yahoo tickers are
# internal fetch details only — NEVER the RawSignal.symbol.
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset(
    {"US500", "US100", "J225", "HK50", "AU200", "AU200AU"}
)


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace(".", "")


def _is_rebalance_bar(bars: list[BarView]) -> bool:
    """True when the newest bar is the FIRST 1D bar of a new UTC calendar month.

    Same pattern as ``tsmom_12_1_multiasset._is_rebalance_bar`` — a month rollover
    (prev.month != last.month) marks the monthly rebalance, so the strategy emits
    once per month per symbol, never daily. Pure / total.
    """
    last_m = datetime.fromtimestamp(bars[-1].ts, tz=UTC)
    prev_m = datetime.fromtimestamp(bars[-2].ts, tz=UTC)
    return (last_m.year, last_m.month) != (prev_m.year, prev_m.month)


def _relative_rank(symbol: str, own_roc: float, peer_roc: dict[str, float]) -> int | None:
    """1-based rank of ``symbol`` within the peer ROC_120 map (None if not present).

    Higher ROC_120 → better (lower) rank. The own symbol's ROC is used when the map
    omits it (degrade-safe). Returns None only when the peer map is empty/invalid.
    """
    if not peer_roc:
        return None
    merged: dict[str, float] = {_norm_symbol(k): float(v) for k, v in peer_roc.items()}
    merged[_norm_symbol(symbol)] = own_roc
    ordered = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
    target = _norm_symbol(symbol)
    for idx, (sym, _roc) in enumerate(ordered):
        if sym == target:
            return idx + 1
    return None


class IndexDualMomentumRotationStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="index_dual_momentum_rotation",
        timeframe="1D",
        warmup_bars=ROC_LOOKBACK + 1,  # 121
        max_positions=2,
        gross_cap=0.30,
        per_symbol_cap=0.16,
        expected_holding_bars=21,  # ≈ 1 month roll
        asset_class="index",
        venue="capital",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="cfd_index_dual_momentum",
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
        if len(bars) < ROC_LOOKBACK + 1:
            return None
        # 🚨 CADENCE: emit ONLY on the monthly rebalance-boundary bar.
        if not _is_rebalance_bar(bars):
            return None
        last = bars[-1]

        # ROC_120 (6-month relative-momentum dimension). NOT pre-fed → recompute.
        base_close = bars[-(ROC_LOOKBACK + 1)].close
        if base_close <= 0.0:
            return None
        roc_120 = last.close / base_close - 1.0
        # ABSOLUTE-momentum gate (the load-bearing drawdown-reducing alpha).
        if roc_120 <= 0.0:
            return None

        # RELATIVE top-2 gate — only when the peer ROC_120 map is wired into
        # extra. Absent it, DEGRADE to absolute-gate-only (degrade-never-crash).
        peer_raw = market_view.extra.get("peer_roc_120")
        peer_roc: dict[str, float] = (
            {str(k): float(v) for k, v in peer_raw.items()}
            if isinstance(peer_raw, dict)
            else {}
        )
        rank = _relative_rank(market_view.symbol, roc_120, peer_roc)
        if rank is not None:
            if rank > TOP_N:
                return None  # outside top-2 → no emit (cash safe-harbor)
            strength = RANK1_SCORE if rank == 1 else RANK2_SCORE
            rank_tag = str(rank)
        else:
            # Absolute-gate-only fallback: size off the absolute momentum.
            strength = min(1.0, max(STRENGTH_FLOOR, STRENGTH_FLOOR + ROC_STRENGTH_GAIN * roc_120))
            rank_tag = "abs_only"
        strength = min(1.0, max(STRENGTH_FLOOR, strength))

        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"dual_momentum+roc_120={roc_120:.4f}+rank={rank_tag}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={"leverage_max": LEVERAGE_MAX},
            created_at_bar=last.ts,
            tags={
                "roc_120": f"{roc_120:.4f}",
                "rank": rank_tag,
                "rebalance": "monthly",
                "leverage": f"{int(LEVERAGE_MAX)}",
            },
        )


def _peer_roc_map(views: dict[str, Any]) -> dict[str, float]:
    """Build a {symbol: ROC_120} map from a {symbol: MarketView} dict (helper for a
    future dispatcher peer feed). Pure — recomputes ROC_120 from each view's bars;
    skips views with too little history. Not wired by the current dispatcher."""
    out: dict[str, float] = {}
    for sym, mv in views.items():
        bars = getattr(mv, "bars", None)
        if not bars or len(bars) < ROC_LOOKBACK + 1:
            continue
        base = bars[-(ROC_LOOKBACK + 1)].close
        if base <= 0.0:
            continue
        out[_norm_symbol(sym)] = bars[-1].close / base - 1.0
    return out


__all__ = [
    "ATR_STOP_MULT",
    "IndexDualMomentumRotationStrategy",
    "LEVERAGE_MAX",
    "RANK1_SCORE",
    "RANK2_SCORE",
    "ROC_LOOKBACK",
    "ROC_STRENGTH_GAIN",
    "STRENGTH_FLOOR",
    "SUPPORTED_SYMBOLS",
    "TOP_N",
    "TTL_BARS",
]
