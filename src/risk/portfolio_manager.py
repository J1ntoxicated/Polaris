"""PortfolioManager — single account portfolio (Phase 20.3).

ONE cash balance, ONE position per ticker (AggregatedPosition with N
contribution slices). Signals from N strategies aggregate into a unified
portfolio view that maps to a single live OKX account.

Key API:
    pm = PortfolioManager(starting_cash_usd=5000)
    pm.process_signal(signal, strategy_name, hypo_id, ticker, current_price,
                       fill_price, exit_strategies, regime) → Optional[Contribution]
    pm.partial_close(contribution_id, exit_price, ts_ms, reason, fraction)
    pm.equity(prices) → total cash + open MTM
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Optional

from src.exec.exit_strategies import ExitStrategy
from src.risk.portfolio_state import (
    AggregatedPosition,
    Contribution,
    make_contribution_id,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PortfolioConfig:
    """Per-portfolio risk caps."""
    max_per_ticker_usd: float = 1500.0       # max aggregated size per ticker
    max_per_strategy_pct: float = 0.20       # any strategy ≤ 20% of equity
    max_total_pct: float = 0.95              # cash buffer 5%


class PortfolioManager:
    """Single-account portfolio — aggregates contributions across strategies."""

    def __init__(
        self,
        starting_cash_usd: float = 5000.0,
        config: Optional[PortfolioConfig] = None,
    ) -> None:
        if starting_cash_usd <= 0:
            raise ValueError(f"starting_cash must be > 0, got {starting_cash_usd}")
        self._starting_cash = starting_cash_usd
        self._cash = starting_cash_usd
        self._positions: dict[str, AggregatedPosition] = {}
        self._closed_contributions: list[Contribution] = []
        self.config = config or PortfolioConfig()

    # ── Read-only views ─────────────────────────────────────────────────────

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def starting_cash(self) -> float:
        return self._starting_cash

    @property
    def positions(self) -> dict[str, AggregatedPosition]:
        return dict(self._positions)

    @property
    def n_open_contributions(self) -> int:
        return sum(p.n_open for p in self._positions.values())

    def equity(self, current_prices: dict[str, float]) -> float:
        """Total = cash + open MTM (per-ticker mark-to-market)."""
        total = self._cash
        for ticker, pos in self._positions.items():
            price = current_prices.get(ticker)
            if price is None:
                # No price → use entry as conservative
                total += pos.total_size_usd
            else:
                for c in pos.contributions:
                    if c.is_closed:
                        continue
                    total += c.size_usd * (1 + c.unrealized_pct(price))
        return total

    def get_position(self, ticker: str) -> Optional[AggregatedPosition]:
        return self._positions.get(ticker)

    def get_contribution(
        self, contribution_id: str,
    ) -> tuple[Optional[Contribution], Optional[str]]:
        """Find (contribution, ticker) — None if not found."""
        for ticker, pos in self._positions.items():
            for c in pos.contributions:
                if c.contribution_id == contribution_id and not c.is_closed:
                    return c, ticker
        return None, None

    def realized_pnl_usd(self) -> float:
        return sum(c.realized_net_usd for c in self._closed_contributions)

    def closed_contributions(self) -> tuple[Contribution, ...]:
        return tuple(self._closed_contributions)

    # ── Signal processing (entry path) ──────────────────────────────────────

    def process_entry(
        self,
        ticker: str,
        strategy_name: str,
        hypo_id: str,
        size_usd: float,
        fill_price: float,
        ts_ms: int,
        exit_strategies: tuple[ExitStrategy, ...],
        fee_round_trip: float = 0.002,
        signal_confidence: float = 0.0,
        signal_reason: str = "",
        regime: str = "",
    ) -> Optional[Contribution]:
        """Add a new contribution (long entry). Returns None if rejected.

        Pre-trade checks:
            - cash sufficient
            - per-ticker cap not exceeded
        """
        if size_usd <= 0 or fill_price <= 0:
            return None
        if size_usd > self._cash:
            logger.info(
                f"[PORTFOLIO] entry rejected — insufficient cash "
                f"(need ${size_usd:.2f}, have ${self._cash:.2f})"
            )
            return None
        # Per-ticker cap
        existing = self._positions.get(ticker)
        existing_size = existing.total_size_usd if existing else 0.0
        if existing_size + size_usd > self.config.max_per_ticker_usd:
            logger.info(
                f"[PORTFOLIO] entry rejected — ticker cap "
                f"{ticker} existing=${existing_size:.0f} + new=${size_usd:.0f} "
                f"> cap=${self.config.max_per_ticker_usd:.0f}"
            )
            return None

        # Build contribution
        cid = make_contribution_id(ticker, strategy_name, ts_ms)
        contrib = Contribution(
            contribution_id=cid,
            ticker=ticker,
            strategy_name=strategy_name,
            hypo_id=hypo_id,
            direction=1,
            entry_price=fill_price,
            size_usd=size_usd,
            base_qty=size_usd / fill_price,
            open_ts_ms=ts_ms,
            fee_round_trip=fee_round_trip,
            exit_strategies=exit_strategies,
            high_since_entry=fill_price,  # peak starts at entry
            signal_confidence=signal_confidence,
            signal_reason=signal_reason,
            regime=regime,
        )

        # Commit: deduct cash, add to AggregatedPosition
        self._cash -= size_usd
        if existing is None:
            self._positions[ticker] = AggregatedPosition(
                ticker=ticker, contributions=(contrib,),
            )
        else:
            self._positions[ticker] = existing.add_contribution(contrib)
        return contrib

    # ── Position lifecycle (exit path) ──────────────────────────────────────

    def update_high_water(self, ticker: str, price: float) -> None:
        """Track peak per contribution (for trailing stops)."""
        pos = self._positions.get(ticker)
        if pos is None:
            return
        new_contribs = tuple(
            c.with_high_since_entry(price) if not c.is_closed else c
            for c in pos.contributions
        )
        self._positions[ticker] = replace(pos, contributions=new_contribs)

    def partial_close(
        self,
        contribution_id: str,
        exit_price: float,
        ts_ms: int,
        reason: str,
        fraction: float = 1.0,
    ) -> Optional[Contribution]:
        """Close a contribution slice. Returns the closed slice (with PnL)."""
        contrib, ticker = self.get_contribution(contribution_id)
        if contrib is None or ticker is None:
            return None
        remaining, closed = contrib.closed(
            exit_price=exit_price, close_ts_ms=ts_ms,
            reason=reason, fraction=fraction,
        )
        # Cash settlement: return size_usd of closed slice + realized net
        self._cash += closed.size_usd + closed.realized_net_usd
        # Update AggregatedPosition
        pos = self._positions[ticker]
        if remaining.size_usd <= 1e-9:
            # Fully closed — remove
            new_pos = pos.remove_contribution(contrib.contribution_id)
        else:
            # Partial — replace with remaining slice
            new_pos = pos.replace_contribution(contrib.contribution_id, remaining)
        if new_pos.n_open == 0:
            # No contributions left → drop ticker
            self._positions.pop(ticker, None)
        else:
            self._positions[ticker] = new_pos
        # Audit trail
        self._closed_contributions.append(closed)
        return closed

    def mark_partial_fired(self, contribution_id: str, level_pct: float) -> None:
        """Track which PartialTP levels have already fired."""
        contrib, ticker = self.get_contribution(contribution_id)
        if contrib is None or ticker is None:
            return
        new_contrib = contrib.with_partial_fired(level_pct)
        self._positions[ticker] = self._positions[ticker].replace_contribution(
            contribution_id, new_contrib,
        )
