"""PositionPolicy — adaptive per-position exit logic (Phase 21).

Position is a dynamic state machine. Static `exit_strategies` set at entry
is inadequate: market regime changes, profit accrues, contributions merge.
A PositionPolicy is evaluated per tick and can:

- HOLD: keep current exit_strategies, run them
- UPDATE_EXITS: replace exit_strategies with new context-aware list
- EXIT_FULL / EXIT_PARTIAL: directly close (rare — usually let exits decide)

Default: CompositePolicy([MergeAdaptive, RegimeAdaptive, TrailingProfit]) —
position re-evaluates exits on merge, on regime shift, when profit accrues.

Reference: real algo trading (Freqtrade custom_exit, ML-based exit policies).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.exec.exit_strategies import ExitStrategy


class PolicyAction(str, Enum):
    HOLD = "hold"             # keep current exits, do nothing
    UPDATE_EXITS = "update"   # replace exits with new list
    EXIT_FULL = "exit_full"   # close 100% now
    EXIT_PARTIAL = "exit_partial"  # close fraction now


@dataclass(frozen=True)
class MarketContext:
    """Per-tick market state for policy evaluation."""
    ticker: str
    price: float
    ts_ms: int
    regime: str = "flat"           # uptrend / downtrend / flat / crisis / unknown
    volatility_atr_pct: float = 0.0
    funding_8h: float = 0.0
    btc_trend: str = "unknown"     # up / down / unknown
    last_signal_action: Optional[str] = None


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome of PositionPolicy.evaluate()."""
    action: PolicyAction
    new_exits: tuple[ExitStrategy, ...] = ()
    fraction: float = 1.0
    reason: str = ""

    def __post_init__(self) -> None:
        if self.action == PolicyAction.UPDATE_EXITS and not self.new_exits:
            raise ValueError("UPDATE_EXITS requires non-empty new_exits")
        if self.action == PolicyAction.EXIT_PARTIAL and not (0 < self.fraction < 1.0):
            raise ValueError(f"EXIT_PARTIAL fraction must be in (0,1), got {self.fraction}")


# Forward declaration for type hints (avoids circular import).
# Concrete AggregatedPosition is in src/risk/portfolio_state.py
class _PositionLike:
    """Duck-type protocol — anything with contributions, ticker, total_size_usd."""
    pass


class PositionPolicy(ABC):
    """Abstract — adaptive exit policy for a single AggregatedPosition."""

    name: str = "abstract"

    @abstractmethod
    def evaluate(
        self,
        position,  # AggregatedPosition (forward ref)
        market: MarketContext,
    ) -> PolicyDecision:
        ...


# ─── StaticPolicy ─── (Phase 20 default behavior preserved) ─────────────────


@dataclass(frozen=True)
class StaticPolicy(PositionPolicy):
    """No-op policy — keeps initial exit_strategies fixed (Phase 20 behavior)."""
    name: str = "static"

    def evaluate(self, position, market: MarketContext) -> PolicyDecision:
        return PolicyDecision(action=PolicyAction.HOLD, reason="static")


# ─── CompositePolicy ─── (run multiple policies in sequence) ────────────────


@dataclass(frozen=True)
class CompositePolicy(PositionPolicy):
    """Run multiple policies in declared order. First non-HOLD wins.

    Use to chain Merge → Regime → Trailing adaptations.
    """
    policies: tuple[PositionPolicy, ...]
    name: str = "composite"

    def evaluate(self, position, market: MarketContext) -> PolicyDecision:
        for p in self.policies:
            decision = p.evaluate(position, market)
            if decision.action != PolicyAction.HOLD:
                return decision
        return PolicyDecision(action=PolicyAction.HOLD, reason="composite_all_hold")
