"""Adaptive PositionPolicy implementations (Phase 21.2-21.3).

Composable policies that adapt position exit logic to:
- Contribution merges (MergeAdaptivePolicy)
- Market regime shifts (RegimeAdaptivePolicy)
- Profit zone entry (TrailingProfitPolicy)

All P6 pure (deterministic on inputs). State tracking via dataclasses;
PositionManager owns the position object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.exec.exit_strategies import (
    ExitStrategy,
    StopLoss,
    TakeProfit,
    TimeBasedHold,
    TrailingStop,
)
from src.risk.exit_merger import merge_exits
from src.risk.position_policy import (
    MarketContext,
    PolicyAction,
    PolicyDecision,
    PositionPolicy,
)


# ─── MergeAdaptivePolicy ────────────────────────────────────────────────────


@dataclass
class MergeAdaptivePolicy(PositionPolicy):
    """When new contribution joins, recompute UNIFIED exit_strategies.

    Real trading insight: 1 BTC = 1 BTC. Multi-strategy entry = high
    conviction. Don't slice — capture full move with combined exit.

    State: tracks last seen contribution count to detect merge.
    """
    name: str = "merge_adaptive"
    _last_contrib_count: int = 0

    def evaluate(self, position, market: MarketContext) -> PolicyDecision:
        active = [c for c in position.contributions if not c.is_closed]
        n = len(active)
        # Single contribution → no merge needed
        if n <= 1:
            self._last_contrib_count = n
            return PolicyDecision(action=PolicyAction.HOLD, reason="single_contrib")
        # Merge detected (count grew)
        if n != self._last_contrib_count:
            unified = merge_exits([c.exit_strategies for c in active])
            self._last_contrib_count = n
            return PolicyDecision(
                action=PolicyAction.UPDATE_EXITS,
                new_exits=unified,
                reason=f"merge:{n}_contribs_unified",
            )
        return PolicyDecision(action=PolicyAction.HOLD, reason="merge_no_change")


# ─── RegimeAdaptivePolicy ───────────────────────────────────────────────────


@dataclass
class RegimeAdaptivePolicy(PositionPolicy):
    """Adjust exits based on BTC regime.

    Regime → exit profile:
        crisis    → tighten SL (high vol, fast exits), wide TP (mean revert spike)
        downtrend → tight SL, smaller TP (limit damage)
        flat      → standard (default profile preserved)
        uptrend   → loosen SL, expand TP (let winners run)

    Doesn't fire if exits already match the regime profile (idempotent).
    """
    name: str = "regime_adaptive"
    _last_regime: str = ""

    def evaluate(self, position, market: MarketContext) -> PolicyDecision:
        if market.regime == self._last_regime or market.regime in ("", "unknown"):
            return PolicyDecision(action=PolicyAction.HOLD, reason="no_regime_change")

        # Only adapt if position has any active contribution
        if not any(not c.is_closed for c in position.contributions):
            return PolicyDecision(action=PolicyAction.HOLD, reason="no_open")

        new_exits = self._exits_for_regime(market.regime)
        if not new_exits:
            return PolicyDecision(action=PolicyAction.HOLD, reason="regime_default")
        self._last_regime = market.regime
        return PolicyDecision(
            action=PolicyAction.UPDATE_EXITS,
            new_exits=new_exits,
            reason=f"regime_shift:{market.regime}",
        )

    @staticmethod
    def _exits_for_regime(regime: str) -> tuple[ExitStrategy, ...]:
        if regime == "crisis":
            # Crisis: high vol mean-revert. Tight SL, wide TP, short hold.
            return (TakeProfit(0.025), StopLoss(0.005), TimeBasedHold(2.0))
        if regime == "downtrend":
            # Downtrend: limit damage. Tight SL, modest TP, short hold.
            return (TakeProfit(0.01), StopLoss(0.005), TimeBasedHold(2.0))
        if regime == "uptrend":
            # Uptrend: let winners run. Loose SL, big TP, long hold.
            return (TakeProfit(0.08), StopLoss(0.025), TimeBasedHold(96.0))
        # flat: keep prior exits (return empty → HOLD)
        return ()


# ─── TrailingProfitPolicy ───────────────────────────────────────────────────


@dataclass
class MicroProfitPolicy(PositionPolicy):
    """Take first viable profit > fee + slippage + cushion (Phase 22.1).

    Philosophy: don't hold for big targets. Cycle capital fast — small
    wins compound. Daily 0.5% goal via volume of small profitable trades.

    min_profit_pct breakdown (default 0.005 = 0.5%):
        fee_round_trip:  0.0020  (OKX taker 0.1% × 2)
        slippage_est:    0.0005  (5bps avg observed)
        cushion:         0.0025  (safety + clean win)
        total:           0.0050

    EXIT_FULL when weighted unrealized >= min_profit_pct.
    """
    min_profit_pct: float = 0.005
    name: str = "micro_profit"

    def evaluate(self, position, market: MarketContext) -> PolicyDecision:
        active = [c for c in position.contributions if not c.is_closed]
        if not active:
            return PolicyDecision(action=PolicyAction.HOLD, reason="no_active")
        total_size = sum(c.size_usd for c in active)
        if total_size <= 0:
            return PolicyDecision(action=PolicyAction.HOLD, reason="zero_size")
        weighted_gain = sum(
            c.unrealized_pct(market.price) * c.size_usd for c in active
        ) / total_size
        if weighted_gain >= self.min_profit_pct:
            return PolicyDecision(
                action=PolicyAction.EXIT_FULL,
                reason=f"micro_profit:{weighted_gain*100:.3f}%>={self.min_profit_pct*100:.2f}%",
            )
        return PolicyDecision(action=PolicyAction.HOLD, reason="below_micro_threshold")


@dataclass
class TrailingProfitPolicy(PositionPolicy):
    """Once position reaches activation profit, switch to trailing-only exits.

    Logic:
        - Position not yet in profit zone → HOLD (keep TP/SL/MaxHold)
        - Position crossed activation_pct profit → switch exits to:
              (TrailingStop(activation, trail), TimeBasedHold(max_hold))
          This locks in gains, lets winners ride.

    State: tracks whether trailing was activated for this position.
    """
    activation_pct: float = 0.005   # +0.5% triggers trailing mode
    trail_pct: float = 0.003        # 0.3% trail
    max_hold_h: float = 24.0
    name: str = "trailing_profit"
    _activated: bool = False

    def evaluate(self, position, market: MarketContext) -> PolicyDecision:
        if self._activated:
            return PolicyDecision(action=PolicyAction.HOLD, reason="already_trailing")
        # Compute weighted-avg unrealized gain across active contributions
        active = [c for c in position.contributions if not c.is_closed]
        if not active:
            return PolicyDecision(action=PolicyAction.HOLD, reason="no_active")
        total_size = sum(c.size_usd for c in active)
        if total_size <= 0:
            return PolicyDecision(action=PolicyAction.HOLD, reason="zero_size")
        weighted_gain = sum(
            c.unrealized_pct(market.price) * c.size_usd for c in active
        ) / total_size
        if weighted_gain < self.activation_pct:
            return PolicyDecision(action=PolicyAction.HOLD, reason="not_in_profit_zone")

        new_exits = (
            TrailingStop(
                activation_pct=self.activation_pct,
                trail_pct=self.trail_pct,
            ),
            TimeBasedHold(self.max_hold_h),
        )
        self._activated = True
        return PolicyDecision(
            action=PolicyAction.UPDATE_EXITS,
            new_exits=new_exits,
            reason=f"trailing_activated:gain={weighted_gain*100:.2f}%",
        )


# ─── Default factory ────────────────────────────────────────────────────────


def build_default_composite() -> PositionPolicy:
    """Factory — default CompositePolicy: Merge → Regime.

    Phase 23.6 (Codex round-1 final): MicroProfit removed from default chain.
    Profit-take logic moved INTO PortfolioPolicyManager (PM orchestrator) and
    only fires conditionally — when PositionEvaluator classifies as COLD.

    HOT/WARM positions HOLD (potential remains, don't fragment).
    COLD positions either profit-take or rotate (handled by PM orchestrator).

    Order:
        1. MergeAdaptive — re-unify exits when contribution joins
        2. RegimeAdaptive — adjust exits to regime if appropriate

    User vision realized: "포텐셜 있음 들고있고, 횡보면 갈아치워."
    """
    from src.risk.position_policy import CompositePolicy
    return CompositePolicy(
        policies=(
            MergeAdaptivePolicy(),
            RegimeAdaptivePolicy(),
        ),
    )


def build_aggressive_composite() -> PositionPolicy:
    """Factory — even tighter cycling for high-velocity environments.

    min_profit_pct = 0.003 (just barely above fee+slip).
    Use only on high-frequency strategies (HYPO-008 VolBurst, HYPO-040 Grid).
    """
    from src.risk.position_policy import CompositePolicy
    return CompositePolicy(
        policies=(
            MergeAdaptivePolicy(),
            MicroProfitPolicy(min_profit_pct=0.003),
            RegimeAdaptivePolicy(),
        ),
    )
