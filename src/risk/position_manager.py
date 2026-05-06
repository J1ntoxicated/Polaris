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
from src.risk.position_policy import (
    MarketContext,
    PolicyAction,
    PositionPolicy,
    StaticPolicy,
)

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
    """Real-time monitor — runs PositionPolicy + exit_strategies per tick.

    Phase 21: each ticker has an attached PositionPolicy that adapts exits
    over time (merge / regime / trailing). Default StaticPolicy preserves
    Phase 20 behavior.

    Per-tick flow:
        1. Build MarketContext from price + regime + indicators
        2. policy.evaluate(position, context) → decision
        3. If UPDATE_EXITS: replace contributions' exit_strategies in place
        4. If EXIT_FULL/PARTIAL: directly close (rare path)
        5. Else (HOLD): run static exit_strategies (Phase 20 logic)
    """

    def __init__(self, portfolio: PortfolioManager) -> None:
        self.portfolio = portfolio
        # Per-ticker policy. Default StaticPolicy when not assigned.
        self._policies: dict[str, PositionPolicy] = {}

    def assign_policy(self, ticker: str, policy: PositionPolicy) -> None:
        """Attach a policy to a ticker. Called at first entry."""
        self._policies[ticker] = policy

    def get_policy(self, ticker: str) -> PositionPolicy:
        return self._policies.get(ticker, StaticPolicy())

    def check_exits(
        self,
        current_prices: dict[str, float],
        ts_ms: int,
        last_signal_actions: dict[tuple[str, str], str] | None = None,
        market_contexts: dict[str, MarketContext] | None = None,
    ) -> list[ExitEvent]:
        """Evaluate all open contributions; close those whose exit fires.

        current_prices: {ticker: latest tick price}
        ts_ms: tick timestamp
        last_signal_actions: optional {(ticker, strategy_name): latest_action}
                             for SignalReversal — per-ticker isolated to avoid
                             cross-ticker false EXIT triggers.

        Returns list of ExitEvent (one per fire). Side effects on self.portfolio.
        """
        last_signal_actions = last_signal_actions or {}
        market_contexts = market_contexts or {}
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

            # Phase 21: PositionPolicy.evaluate — adaptive exit logic.
            ctx = market_contexts.get(ticker) or MarketContext(
                ticker=ticker, price=price, ts_ms=ts_ms,
            )
            policy = self.get_policy(ticker)
            decision = policy.evaluate(pos, ctx)

            if decision.action == PolicyAction.UPDATE_EXITS:
                # Replace each open contribution's exit_strategies with new ones.
                # Maintains attribution but unifies exit logic.
                self._update_position_exits(pos, decision.new_exits)
                logger.info(
                    f"[POLICY-UPDATE] {ticker} reason={decision.reason} "
                    f"new_exits={[e.name for e in decision.new_exits]}"
                )
                # Re-fetch after update
                pos = self.portfolio.get_position(ticker)
                if pos is None:
                    continue
            elif decision.action == PolicyAction.EXIT_FULL:
                # Force close all contributions
                for contrib in list(pos.contributions):
                    if contrib.is_closed:
                        continue
                    market_for_exit = MarketSnapshot(
                        ticker=ticker, price=price, ts_ms=ts_ms,
                        high_since_entry=contrib.high_since_entry,
                    )
                    ex_decision = ExitDecision(
                        should_close=True, reason=decision.reason or "policy_full",
                        fraction=1.0,
                    )
                    ev = self._execute_exit(contrib, ex_decision, price, ts_ms)
                    if ev:
                        events.append(ev)
                continue  # don't run static check on closed
            elif decision.action == PolicyAction.EXIT_PARTIAL:
                # Force partial close — apply to first open contribution
                for contrib in list(pos.contributions):
                    if contrib.is_closed:
                        continue
                    ex_decision = ExitDecision(
                        should_close=True, reason=decision.reason or "policy_partial",
                        fraction=decision.fraction,
                    )
                    ev = self._execute_exit(contrib, ex_decision, price, ts_ms)
                    if ev:
                        events.append(ev)
                    break

            # Static exit check (HOLD path or after UPDATE_EXITS)
            for contrib in list(pos.contributions):
                if contrib.is_closed:
                    continue
                # Phase 20.7 (Codex P1 fix): isolate by (ticker, strategy)
                # so cross-ticker EXIT doesn't bleed into other contributions.
                last_action = last_signal_actions.get(
                    (ticker, contrib.strategy_name)
                )
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

    def _update_position_exits(self, pos, new_exits: tuple) -> None:
        """Replace exit_strategies on every active contribution.

        Preserves per-contribution SignalReversal exits — these are the
        owning strategy's voice and must survive policy updates. Other
        exits (TP/SL/TimeBased/Trailing/PartialTP) get replaced from the
        unified policy decision.
        """
        from dataclasses import replace as _replace
        from src.exec.exit_strategies import SignalReversal
        for contrib in list(pos.contributions):
            if contrib.is_closed:
                continue
            # Preserve this contribution's SignalReversal(s)
            preserved_sigrev = tuple(
                e for e in contrib.exit_strategies if isinstance(e, SignalReversal)
            )
            # Drop any SignalReversal in new_exits (don't double-add)
            new_non_sigrev = tuple(
                e for e in new_exits if not isinstance(e, SignalReversal)
            )
            merged = new_non_sigrev + preserved_sigrev
            updated = _replace(contrib, exit_strategies=merged)
            self.portfolio._positions[pos.ticker] = pos.replace_contribution(
                contrib.contribution_id, updated,
            )
            pos = self.portfolio.get_position(pos.ticker)
            if pos is None:
                break

    def _execute_exit(
        self,
        contrib,
        decision: ExitDecision,
        exit_price: float,
        ts_ms: int,
    ) -> "ExitEvent | None":
        """Apply decision via portfolio.partial_close + broker SELL.

        Phase 20.7 (Codex P0 fix): broker.place_order(SELL) called BEFORE
        portfolio.partial_close so live exchange position is reduced. For
        PaperBroker this is a noop simulation; for OKXBroker it sends real
        sell order. Without this, live OKX position grows unbounded as
        Polaris's internal ledger drains.
        """
        # If PartialTP fired, mark the level so it doesn't re-fire next tick
        if "partial_tp" in decision.reason:
            try:
                level_str = decision.reason.split(":")[1].split("%")[0]
                level_pct = float(level_str) / 100.0
                self.portfolio.mark_partial_fired(
                    contrib.contribution_id, level_pct,
                )
            except (IndexError, ValueError):
                pass

        # Phase 20.7 — execute broker SELL (live exchange close).
        # Exit notional: base_qty × current_price × fraction (USD value at exit)
        exit_notional_usd = (
            contrib.base_qty * exit_price * decision.fraction
        )
        try:
            from src.exec.broker import OrderRequest, OrderSide, OrderStatus, OrderType
            from src.paper.realtime_runner import get_broker
            _result = get_broker().place_order(OrderRequest(
                side=OrderSide.SELL,
                ticker=contrib.ticker,
                size_usd=exit_notional_usd,
                order_type=OrderType.MARKET,
                client_order_id=f"{contrib.contribution_id[:24]}exit",
            ))
            if _result.status == OrderStatus.FILLED and _result.avg_fill_price > 0:
                # Use broker's actual fill price for partial_close
                exit_price_actual = _result.avg_fill_price
            else:
                logger.warning(
                    f"[EXIT-BROKER-FAIL] {contrib.ticker} {_result.error_msg} "
                    f"— using tick price for ledger settlement"
                )
                exit_price_actual = exit_price
        except Exception as e:
            logger.warning(f"[EXIT-BROKER-ERR] {contrib.ticker}: {e!r}")
            exit_price_actual = exit_price

        closed = self.portfolio.partial_close(
            contribution_id=contrib.contribution_id,
            exit_price=exit_price_actual,
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
            exit_price=exit_price_actual,
            realized_net_usd=closed.realized_net_usd,
            ts_ms=ts_ms,
        )
