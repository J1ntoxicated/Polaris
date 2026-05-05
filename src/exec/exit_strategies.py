"""ExitStrategy framework — composable per-position exit logic (P6 pure).

Phase 20.1 — separates exit decisions from entry strategy. Each contribution
(per-strategy position slice) carries a list of ExitStrategy. PositionManager
evaluates them every tick; first fire → partial or full close.

Variants implemented:
    TakeProfit(pct)              — close when price reaches +pct
    StopLoss(pct)                — close when price reaches -pct
    TrailingStop(activation_pct, trail_pct) — trails after activation
    TimeBasedHold(max_hours)     — close after duration
    SignalReversal(strategy_name) — close when source signal flips
    PartialTP(levels)            — staged profit taking

ExitDecision is a frozen result. ExitStrategy.should_exit is pure.
build_default_exits(profile) factory replaces the static exit_profiles dict.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MarketSnapshot:
    """Per-tick market state passed to exit strategies."""
    ticker: str
    price: float           # current tick price
    ts_ms: int
    # optional context
    high_since_entry: Optional[float] = None  # for trailing stop
    last_signal_action: Optional[str] = None  # for signal-reversal exit


@dataclass(frozen=True)
class ExitDecision:
    """Outcome of ExitStrategy.should_exit()."""
    should_close: bool
    reason: str            # human readable, also stored in trade ledger
    fraction: float = 1.0  # 1.0 = full close, 0.5 = partial


# ─── Abstract base ──────────────────────────────────────────────────────────


class ExitStrategy(ABC):
    """Abstract — exit decision for a single contribution slice (P6 pure)."""

    name: str = "abstract"

    @abstractmethod
    def should_exit(
        self,
        entry_price: float,
        size_usd: float,
        open_ts_ms: int,
        market: MarketSnapshot,
    ) -> ExitDecision:
        ...


# ─── Implementations ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TakeProfit(ExitStrategy):
    """Close when price ≥ entry × (1 + pct). pct in decimal (0.006 = 0.6%)."""
    pct: float
    name: str = "take_profit"

    def __post_init__(self) -> None:
        if self.pct <= 0:
            raise ValueError(f"TakeProfit pct must be > 0, got {self.pct}")

    def should_exit(
        self, entry_price: float, size_usd: float, open_ts_ms: int,
        market: MarketSnapshot,
    ) -> ExitDecision:
        if entry_price <= 0:
            return ExitDecision(False, "")
        gain = (market.price - entry_price) / entry_price
        if gain >= self.pct:
            return ExitDecision(
                True, f"tp_hit:{gain:+.4f}>={self.pct:+.4f}", fraction=1.0,
            )
        return ExitDecision(False, "")


@dataclass(frozen=True)
class StopLoss(ExitStrategy):
    """Close when price ≤ entry × (1 - pct). pct positive decimal (0.0035 = 0.35%)."""
    pct: float
    name: str = "stop_loss"

    def __post_init__(self) -> None:
        if self.pct <= 0:
            raise ValueError(f"StopLoss pct must be > 0, got {self.pct}")

    def should_exit(
        self, entry_price: float, size_usd: float, open_ts_ms: int,
        market: MarketSnapshot,
    ) -> ExitDecision:
        if entry_price <= 0:
            return ExitDecision(False, "")
        loss = (market.price - entry_price) / entry_price
        if loss <= -self.pct:
            return ExitDecision(
                True, f"sl_hit:{loss:+.4f}<=-{self.pct:.4f}", fraction=1.0,
            )
        return ExitDecision(False, "")


@dataclass(frozen=True)
class TrailingStop(ExitStrategy):
    """Activate after activation_pct profit, then trail by trail_pct.

    Requires market.high_since_entry to be tracked by caller (PositionManager).
    """
    activation_pct: float    # e.g. 0.005 = activate after +0.5%
    trail_pct: float         # e.g. 0.003 = trail by 0.3% from peak
    name: str = "trailing_stop"

    def __post_init__(self) -> None:
        if self.activation_pct <= 0 or self.trail_pct <= 0:
            raise ValueError("activation_pct + trail_pct must be > 0")

    def should_exit(
        self, entry_price: float, size_usd: float, open_ts_ms: int,
        market: MarketSnapshot,
    ) -> ExitDecision:
        if entry_price <= 0 or market.high_since_entry is None:
            return ExitDecision(False, "")
        peak = market.high_since_entry
        peak_gain = (peak - entry_price) / entry_price
        if peak_gain < self.activation_pct:
            return ExitDecision(False, "")
        # Activated → check trail
        drawdown = (peak - market.price) / peak
        if drawdown >= self.trail_pct:
            return ExitDecision(
                True,
                f"trail_stop peak={peak:.4f} dd={drawdown:+.4f}>={self.trail_pct:.4f}",
                fraction=1.0,
            )
        return ExitDecision(False, "")


@dataclass(frozen=True)
class TimeBasedHold(ExitStrategy):
    """Force close after max_hours regardless of price."""
    max_hours: float
    name: str = "time_based"

    def __post_init__(self) -> None:
        if self.max_hours <= 0:
            raise ValueError(f"max_hours must be > 0, got {self.max_hours}")

    def should_exit(
        self, entry_price: float, size_usd: float, open_ts_ms: int,
        market: MarketSnapshot,
    ) -> ExitDecision:
        max_ms = int(self.max_hours * 3_600_000)
        if market.ts_ms - open_ts_ms >= max_ms:
            held_h = (market.ts_ms - open_ts_ms) / 3_600_000
            return ExitDecision(
                True, f"max_hold:{held_h:.1f}h>={self.max_hours:.1f}h", fraction=1.0,
            )
        return ExitDecision(False, "")


@dataclass(frozen=True)
class SignalReversal(ExitStrategy):
    """Close when source strategy signal flips (e.g. ENTER_LONG → EXIT or HOLD)."""
    strategy_name: str
    name: str = "signal_reversal"

    def should_exit(
        self, entry_price: float, size_usd: float, open_ts_ms: int,
        market: MarketSnapshot,
    ) -> ExitDecision:
        if market.last_signal_action == "EXIT":
            return ExitDecision(
                True, f"signal_exit:{self.strategy_name}", fraction=1.0,
            )
        return ExitDecision(False, "")


@dataclass(frozen=True)
class PartialTP(ExitStrategy):
    """Staged profit taking — close fraction at each level reached.

    levels: list of (gain_pct, fraction_to_close). E.g.:
      [(0.005, 0.33), (0.010, 0.33), (0.015, 0.34)] = close 33% at +0.5%,
      another 33% at +1%, final 34% at +1.5%.

    NOTE: PositionManager must track which levels have already fired
    (via contribution.partial_tp_fired_levels set) to avoid double-firing.
    """
    levels: tuple[tuple[float, float], ...]
    name: str = "partial_tp"

    def __post_init__(self) -> None:
        if not self.levels:
            raise ValueError("PartialTP requires at least one level")
        for pct, frac in self.levels:
            if pct <= 0 or not (0 < frac <= 1):
                raise ValueError(f"invalid level: pct={pct}, frac={frac}")

    def should_exit(
        self, entry_price: float, size_usd: float, open_ts_ms: int,
        market: MarketSnapshot,
    ) -> ExitDecision:
        if entry_price <= 0:
            return ExitDecision(False, "")
        gain = (market.price - entry_price) / entry_price
        # Highest level reached (positionManager filters already-fired)
        for pct, frac in sorted(self.levels, reverse=True):
            if gain >= pct:
                return ExitDecision(
                    True, f"partial_tp:{pct*100:.2f}%×{frac:.0%}", fraction=frac,
                )
        return ExitDecision(False, "")


# ─── Default factory (replaces static exit_profiles.py) ─────────────────────


def build_default_exits(profile: str) -> tuple[ExitStrategy, ...]:
    """Factory — returns default exit list for a profile name.

    Profiles align with src/paper/exit_profiles.py for migration parity.
    """
    if profile == "scalp":
        return (
            TakeProfit(0.006),     # +0.6%
            StopLoss(0.0035),      # -0.35%
            TimeBasedHold(4.0),    # 4h
        )
    if profile == "swing":
        return (
            TakeProfit(0.05),      # +5%
            StopLoss(0.02),        # -2%
            TimeBasedHold(168.0),  # 7d
        )
    if profile == "position":
        return (
            TakeProfit(0.12),      # +12%
            StopLoss(0.04),        # -4%
            TimeBasedHold(720.0),  # 30d
        )
    if profile == "liquidation":
        return (
            TakeProfit(0.015),     # +1.5%
            StopLoss(0.007),       # -0.7%
            TimeBasedHold(0.5),    # 30min
        )
    raise ValueError(f"unknown exit profile: {profile!r}")


def evaluate_all(
    exit_strategies: tuple[ExitStrategy, ...],
    entry_price: float,
    size_usd: float,
    open_ts_ms: int,
    market: MarketSnapshot,
    fired_partial_levels: set[float] = None,
) -> Optional[ExitDecision]:
    """Run all exit strategies; return first fire (or None).

    fired_partial_levels: set of (level_pct,) already triggered by PartialTP
    (caller-tracked). Skips already-fired PartialTP levels to avoid loops.
    """
    fired_partial_levels = fired_partial_levels or set()
    for ex in exit_strategies:
        if isinstance(ex, PartialTP):
            # Filter already-fired levels
            unfired = tuple((pct, frac) for pct, frac in ex.levels if pct not in fired_partial_levels)
            if not unfired:
                continue
            ex_filtered = PartialTP(levels=unfired)
            decision = ex_filtered.should_exit(entry_price, size_usd, open_ts_ms, market)
        else:
            decision = ex.should_exit(entry_price, size_usd, open_ts_ms, market)
        if decision.should_close:
            return decision
    return None
