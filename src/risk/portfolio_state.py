"""Portfolio state — Contribution + AggregatedPosition (P6 pure).

Phase 20.2 — separates per-strategy "slice" (Contribution) from per-ticker
exchange position (AggregatedPosition).

Live OKX SPOT account has 1 position per ticker. Polaris tracks it as
AggregatedPosition with N Contribution slices, each owned by a different
strategy with its own ExitStrategy chain. Real attribution: per-Contribution
PnL is precise; exchange-level PnL is just sum.

Immutable dataclasses, like state.py — state transitions via factory methods.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Optional

from src.exec.exit_strategies import ExitStrategy


@dataclass(frozen=True, slots=True)
class Contribution:
    """A single strategy's slice of a ticker's aggregated position."""
    contribution_id: str       # unique, e.g. "BTC-USDT-vb-1700000000"
    ticker: str
    strategy_name: str
    hypo_id: str
    direction: int             # +1 long, -1 short (Polaris SPOT = 1)
    entry_price: float
    size_usd: float            # USD value at entry
    base_qty: float            # base currency qty at entry (size_usd / entry_price)
    open_ts_ms: int
    fee_round_trip: float
    exit_strategies: tuple[ExitStrategy, ...] = ()
    # Tracking state
    high_since_entry: float = 0.0  # for trailing stop
    fired_partial_levels: tuple[float, ...] = ()  # PartialTP already-fired levels
    # Audit
    signal_confidence: float = 0.0
    signal_reason: str = ""
    regime: str = ""
    # Closed contribution fields (set on close)
    close_ts_ms: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    realized_net_usd: float = 0.0
    is_closed: bool = False

    def __post_init__(self) -> None:
        if self.entry_price <= 0:
            raise ValueError(f"entry_price must be > 0, got {self.entry_price}")
        # is_closed sentinels can have size_usd=0 / base_qty=0 (fully consumed)
        if self.size_usd < 0:
            raise ValueError(f"size_usd must be >= 0, got {self.size_usd}")
        if self.base_qty < 0:
            raise ValueError(f"base_qty must be >= 0, got {self.base_qty}")
        if not self.is_closed and (self.size_usd <= 0 or self.base_qty <= 0):
            raise ValueError(
                f"open contribution requires size_usd>0 + base_qty>0, "
                f"got size_usd={self.size_usd}, base_qty={self.base_qty}"
            )
        if self.direction not in (1, -1):
            raise ValueError(f"direction must be ±1, got {self.direction}")
        if self.open_ts_ms <= 0:
            raise ValueError(f"open_ts_ms must be > 0, got {self.open_ts_ms}")

    def with_high_since_entry(self, price: float) -> "Contribution":
        """Update peak tracking (used by TrailingStop)."""
        if price > self.high_since_entry:
            return replace(self, high_since_entry=price)
        return self

    def with_partial_fired(self, level_pct: float) -> "Contribution":
        """Mark a PartialTP level as fired (don't re-fire)."""
        if level_pct in self.fired_partial_levels:
            return self
        return replace(
            self, fired_partial_levels=self.fired_partial_levels + (level_pct,),
        )

    def closed(
        self, exit_price: float, close_ts_ms: int, reason: str, fraction: float = 1.0,
    ) -> tuple["Contribution", "Contribution"]:
        """Split into (remaining, closed_slice) — returns 2 contributions.

        For full close, remaining has size_usd=0 (caller drops it).
        For partial close, remaining keeps (1-fraction) and closed has fraction.
        """
        if not (0 < fraction <= 1.0):
            raise ValueError(f"fraction must be in (0, 1], got {fraction}")
        closed_size = self.size_usd * fraction
        closed_qty = self.base_qty * fraction
        remaining_size = self.size_usd - closed_size
        remaining_qty = self.base_qty - closed_qty

        # Compute realized net (gross - fee) on closed slice
        gross_pct = self.direction * (exit_price - self.entry_price) / self.entry_price
        gross = closed_size * gross_pct
        fee = closed_size * self.fee_round_trip
        net = gross - fee

        closed_slice = replace(
            self,
            contribution_id=f"{self.contribution_id}-c{int(fraction * 100)}",
            size_usd=closed_size,
            base_qty=closed_qty,
            close_ts_ms=close_ts_ms,
            exit_price=exit_price,
            exit_reason=reason,
            realized_net_usd=net,
            is_closed=True,
        )
        if remaining_size > 1e-9:
            remaining = replace(
                self, size_usd=remaining_size, base_qty=remaining_qty,
            )
        else:
            # Fully closed — remaining is sentinel (caller drops it)
            remaining = replace(self, size_usd=0.0, base_qty=0.0, is_closed=True)
        return remaining, closed_slice

    @property
    def gross_pct_at(self, current_price: Optional[float] = None) -> float:
        # Note: pseudo-property for unrealized — caller passes price.
        # Concrete getter at_unrealized() below.
        return 0.0

    def unrealized_pct(self, current_price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return self.direction * (current_price - self.entry_price) / self.entry_price

    def unrealized_usd(self, current_price: float) -> float:
        return self.size_usd * self.unrealized_pct(current_price)


@dataclass(frozen=True, slots=True)
class AggregatedPosition:
    """All contributions on a single ticker.

    Lives in PortfolioManager.positions[ticker]. Multiple Contributions
    from different strategies aggregate here. Exchange sees a single
    base_qty position; Polaris tracks per-Contribution attribution.
    """
    ticker: str
    contributions: tuple[Contribution, ...] = ()

    @property
    def total_size_usd(self) -> float:
        return sum(c.size_usd for c in self.contributions if not c.is_closed)

    @property
    def total_base_qty(self) -> float:
        return sum(c.base_qty for c in self.contributions if not c.is_closed)

    @property
    def n_open(self) -> int:
        return sum(1 for c in self.contributions if not c.is_closed)

    @property
    def avg_entry_price(self) -> float:
        opens = [c for c in self.contributions if not c.is_closed]
        if not opens:
            return 0.0
        total_cost = sum(c.size_usd for c in opens)
        total_qty = sum(c.base_qty for c in opens)
        return total_cost / total_qty if total_qty > 0 else 0.0

    def add_contribution(self, contrib: Contribution) -> "AggregatedPosition":
        """Add a new contribution slice (entry)."""
        if contrib.ticker != self.ticker:
            raise ValueError(
                f"contribution ticker {contrib.ticker} != position {self.ticker}"
            )
        return replace(self, contributions=self.contributions + (contrib,))

    def replace_contribution(
        self, contribution_id: str, new_contrib: Contribution,
    ) -> "AggregatedPosition":
        """Replace one contribution by id (for state updates like high_since_entry)."""
        new_list = tuple(
            new_contrib if c.contribution_id == contribution_id else c
            for c in self.contributions
        )
        return replace(self, contributions=new_list)

    def remove_contribution(self, contribution_id: str) -> "AggregatedPosition":
        """Drop a contribution (after full close)."""
        return replace(
            self,
            contributions=tuple(
                c for c in self.contributions if c.contribution_id != contribution_id
            ),
        )


def make_contribution_id(ticker: str, strategy_name: str, ts_ms: int) -> str:
    """Deterministic contribution id — used for OKX clOrdId tagging."""
    # OKX clOrdId requires alphanumeric, max 32 chars
    safe_ticker = ticker.replace("-", "").replace("/", "")
    safe_strat = "".join(c for c in strategy_name if c.isalnum())[:8]
    return f"{safe_ticker}{safe_strat}{ts_ms}"[:32]
