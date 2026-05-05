"""PositionManager — real-time exit monitor (Phase 20.4).

Watches all open contributions across the portfolio. Every tick:
  1. Update high_water for trailing stops.
  2. For each contribution, evaluate its exit_strategies in priority order.
  3. First fire → partial_close via PortfolioManager.

Decoupled from Strategy class — entry strategy doesn't know or care about
exit logic. Exits are attached to contributions at entry time and run
independently.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.exec.exit_strategies import (
    ExitDecision,
    MarketSnapshot,
    PartialTP,
    evaluate_all,
)
from src.risk.portfolio_manager import PortfolioManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExitEvent:
    """Records a single exit fire (for logging/metrics)."""
    contribution_id: str
    ticker: str
    strategy_name: str
    exit_reason: str
    fraction: float
    exit_price: float
    realized_net_usd: float
    ts_ms: int


class PositionManager:
    """Real-time monitor — runs exit_strategies on every contribution per tick.

    Stateless wrt exit decisions — relies on Contribution.high_since_entry
    + fired_partial_levels for state, both tracked via PortfolioManager.
    """

    def __init__(self, portfolio: PortfolioManager) -> None:
        self.portfolio = portfolio

    def check_exits(
        self,
        current_prices: dict[str, float],
        ts_ms: int,
        last_signal_actions: dict[str, str] | None = None,
    ) -> list[ExitEvent]:
        """Evaluate all open contributions; close those whose exit fires.

        current_prices: {ticker: latest tick price}
        ts_ms: tick timestamp
        last_signal_actions: optional {strategy_name: latest_action} for
                             SignalReversal exit strategies.

        Returns list of ExitEvent (one per fire). Side effects on self.portfolio.
        """
        last_signal_actions = last_signal_actions or {}
        events: list[ExitEvent] = []

        for ticker, pos in list(self.portfolio.positions.items()):
            price = current_prices.get(ticker)
            if price is None or price <= 0:
                continue
            # Update high water for trailing stops (idempotent if no new high)
            self.portfolio.update_high_water(ticker, price)

            # Re-fetch position after high_water update
            pos = self.portfolio.get_position(ticker)
            if pos is None:
                continue

            for contrib in list(pos.contributions):
                if contrib.is_closed:
                    continue
                last_action = last_signal_actions.get(contrib.strategy_name)
                market = MarketSnapshot(
                    ticker=ticker,
                    price=price,
                    ts_ms=ts_ms,
                    high_since_entry=contrib.high_since_entry,
                    last_signal_action=last_action,
                )
                fired_levels = set(contrib.fired_partial_levels)
                decision = evaluate_all(
                    contrib.exit_strategies,
                    entry_price=contrib.entry_price,
                    size_usd=contrib.size_usd,
                    open_ts_ms=contrib.open_ts_ms,
                    market=market,
                    fired_partial_levels=fired_levels,
                )
                if decision is None:
                    continue
                event = self._execute_exit(contrib, decision, price, ts_ms)
                if event is not None:
                    events.append(event)
        return events

    def _execute_exit(
        self,
        contrib,
        decision: ExitDecision,
        exit_price: float,
        ts_ms: int,
    ) -> "ExitEvent | None":
        """Apply decision via portfolio.partial_close. Track partial_tp levels."""
        # If PartialTP fired, mark the level so it doesn't re-fire next tick
        if "partial_tp" in decision.reason:
            # Parse level from reason (e.g. "partial_tp:0.50%×33%")
            try:
                level_str = decision.reason.split(":")[1].split("%")[0]
                level_pct = float(level_str) / 100.0
                self.portfolio.mark_partial_fired(
                    contrib.contribution_id, level_pct,
                )
            except (IndexError, ValueError):
                pass

        closed = self.portfolio.partial_close(
            contribution_id=contrib.contribution_id,
            exit_price=exit_price,
            ts_ms=ts_ms,
            reason=decision.reason,
            fraction=decision.fraction,
        )
        if closed is None:
            return None
        logger.info(
            f"[POS-EXIT] {contrib.ticker} {contrib.strategy_name} "
            f"contrib={contrib.contribution_id[:16]} reason={decision.reason} "
            f"frac={decision.fraction:.2f} net=${closed.realized_net_usd:+.2f}"
        )
        return ExitEvent(
            contribution_id=contrib.contribution_id,
            ticker=contrib.ticker,
            strategy_name=contrib.strategy_name,
            exit_reason=decision.reason,
            fraction=decision.fraction,
            exit_price=exit_price,
            realized_net_usd=closed.realized_net_usd,
            ts_ms=ts_ms,
        )
