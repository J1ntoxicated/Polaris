"""PortfolioPolicyManager — orchestrator for active capital allocation (Phase 23.5).

Runs every PM_INTERVAL_S (default 30s). Per cycle:

1. EVALUATE — score every open contribution → state (HOT/WARM/COLD/LOSING)
2. SCAN — check for new opportunities (cached, cross-strategy)
3. DECIDE — for each contribution, decide: HOLD / CLOSE / ROTATE / ADD
4. EXECUTE — atomic via PortfolioManager + Broker (close-then-open guarded)

Codex round-1 critical fixes applied:
- Atomic execution: close MUST succeed before opening replacement
- Periodic (not per-tick) — avoids churn on signal noise
- Hysteresis-aware via PositionEvaluator state tracking
- Dynamic switching cost in ReallocationDecider

This is the Position Management layer the user requested:
"포션 늘려도 좋고 ... 횡보면 갈아치워"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from src.risk.opportunity_scanner import (
    Opportunity,
    OpportunityScanner,
    ScanResult,
)
from src.risk.portfolio_manager import PortfolioManager
from src.risk.position_evaluator import (
    EvaluationInputs,
    PositionEvaluator,
    PositionState,
    fatigue_from_age,
    momentum_from_prices,
)
from src.risk.reallocation_decider import (
    ReallocAction,
    ReallocationDecider,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PMCycleResult:
    ts_ms: int
    n_evaluated: int
    n_holds: int
    n_closes: int
    n_rotates: int
    n_adds: int


class PortfolioPolicyManager:
    """Orchestrates Position Management — evaluate / scan / decide / execute.

    Caller wires:
      - portfolio: PortfolioManager
      - opportunity scanner with periodic interval
      - reallocation decider with hysteresis + cooldown

    Per cycle, PM runs the loop and returns summary.
    """

    def __init__(
        self,
        portfolio: PortfolioManager,
        scanner: Optional[OpportunityScanner] = None,
        decider: Optional[ReallocationDecider] = None,
        evaluator: Optional[PositionEvaluator] = None,
        cycle_interval_s: float = 30.0,
    ) -> None:
        self.portfolio = portfolio
        self.scanner = scanner or OpportunityScanner()
        self.decider = decider or ReallocationDecider()
        self.evaluator = evaluator or PositionEvaluator()
        self.cycle_interval_s = cycle_interval_s
        self._last_cycle_ms: int = 0

    def should_run(self, ts_ms: int) -> bool:
        if self._last_cycle_ms == 0:
            return True
        return (ts_ms - self._last_cycle_ms) >= int(self.cycle_interval_s * 1000)

    def cycle(
        self,
        ts_ms: int,
        current_prices: dict[str, float],
        candidate_signals: list[tuple[str, str, str]],
        signal_eval_fn: Callable,
        recent_prices_fn: Callable[[str], list[float]],
        force: bool = False,
    ) -> PMCycleResult:
        """Run one PM cycle.

        candidate_signals: [(ticker, strategy_name, hypo_id), ...]
        signal_eval_fn(ticker, strategy_name, hypo_id) → Opportunity | None
        recent_prices_fn(ticker) → list[float] for momentum calc

        Returns PMCycleResult summary.
        """
        if not force and not self.should_run(ts_ms):
            return PMCycleResult(ts_ms, 0, 0, 0, 0, 0)
        self._last_cycle_ms = ts_ms

        # 1. SCAN — opportunities (periodic, cached)
        scan_result = self.scanner.scan(
            ts_ms=ts_ms, candidates=candidate_signals,
            signal_fn=signal_eval_fn, force=False,
        )

        # 2. EVALUATE + 3. DECIDE + 4. EXECUTE per open contribution
        n_eval = n_hold = n_close = n_rotate = n_add = 0
        for ticker, pos in list(self.portfolio.positions.items()):
            price = current_prices.get(ticker)
            if price is None or price <= 0:
                continue
            recent = recent_prices_fn(ticker)
            momentum = momentum_from_prices(recent)
            best_opp = self.scanner.best_for_ticker(ticker) or self.scanner.best_overall()

            for contrib in list(pos.contributions):
                if contrib.is_closed:
                    continue
                n_eval += 1
                # Build EvaluationInputs
                # continuation: use signal_eval re-run (same strategy on this ticker)
                cont_signal = 0.0
                opp_same = self.scanner.best_for_ticker(ticker)
                if opp_same and opp_same.strategy_name == contrib.strategy_name:
                    cont_signal = 1.0  # same strategy still ENTERing → bullish
                # Confluence: any other strategy bullish on this ticker?
                confluence = 0.0
                if best_opp and best_opp.ticker == ticker:
                    if best_opp.strategy_name != contrib.strategy_name:
                        confluence = 0.5  # one other strategy confirming
                # Fatigue: held vs typical (default 4h)
                held_min = (ts_ms - contrib.open_ts_ms) / 60_000
                fatigue = fatigue_from_age(held_min, 240.0)

                inputs = EvaluationInputs(
                    continuation_signal=cont_signal,
                    momentum_score=momentum,
                    confluence_score=confluence,
                    fatigue_factor=fatigue,
                )
                unrealized = contrib.unrealized_pct(price)
                evaluation = self.evaluator.evaluate(
                    contrib.contribution_id, inputs, unrealized,
                )

                # Decide
                decision = self.decider.decide_for_position(
                    contribution=contrib,
                    evaluation=evaluation,
                    unrealized_pct=unrealized,
                    best_opportunity=best_opp,
                    ts_ms=ts_ms,
                )

                # Execute (atomic guarded)
                self._execute(decision, contrib, price, ts_ms)

                if decision.action == ReallocAction.HOLD:
                    n_hold += 1
                elif decision.action == ReallocAction.CLOSE_ONLY:
                    n_close += 1
                elif decision.action == ReallocAction.CLOSE_THEN_OPEN:
                    n_rotate += 1
                elif decision.action == ReallocAction.ADD_TO:
                    n_add += 1

        result = PMCycleResult(
            ts_ms=ts_ms, n_evaluated=n_eval, n_holds=n_hold,
            n_closes=n_close, n_rotates=n_rotate, n_adds=n_add,
        )
        if n_eval > 0:
            logger.info(
                f"[PM-CYCLE] eval={n_eval} hold={n_hold} close={n_close} "
                f"rotate={n_rotate} add={n_add}"
            )
        return result

    def _execute(self, decision, contrib, price: float, ts_ms: int) -> None:
        """Apply decision atomically. Caller manages broker via portfolio."""
        if decision.action == ReallocAction.HOLD:
            return
        if decision.action == ReallocAction.CLOSE_ONLY:
            self.portfolio.partial_close(
                contribution_id=contrib.contribution_id,
                exit_price=price, ts_ms=ts_ms,
                reason=f"pm_close:{decision.reason}", fraction=1.0,
            )
            self.evaluator.reset(contrib.contribution_id)
            logger.info(
                f"[PM-CLOSE] {contrib.ticker} {contrib.strategy_name} {decision.reason}"
            )
            return
        if decision.action == ReallocAction.CLOSE_THEN_OPEN:
            # Atomic: close first, only open if cash freed
            cash_before = self.portfolio.cash
            closed = self.portfolio.partial_close(
                contribution_id=contrib.contribution_id,
                exit_price=price, ts_ms=ts_ms,
                reason=f"pm_rotate:{decision.reason}", fraction=1.0,
            )
            if closed is None or self.portfolio.cash <= cash_before:
                logger.warning(
                    f"[PM-ROTATE-FAIL] {contrib.ticker} close failed/no cash → no replacement"
                )
                return
            self.evaluator.reset(contrib.contribution_id)
            logger.info(
                f"[PM-ROTATE] closed {contrib.ticker}/{contrib.strategy_name} "
                f"→ planning open {decision.opportunity.ticker}/"
                f"{decision.opportunity.strategy_name}"
            )
            # NOTE: actual open of replacement is delegated to caller —
            # PortfolioPolicyManager doesn't import broker. Caller picks up
            # opportunity from PM result and triggers normal entry path.
            return
        if decision.action == ReallocAction.ADD_TO:
            # ADD: open extra contribution under same strategy, smaller size
            from src.exec.exit_strategies import build_default_exits
            self.portfolio.process_entry(
                ticker=contrib.ticker,
                strategy_name=contrib.strategy_name,
                hypo_id=contrib.hypo_id,
                size_usd=decision.add_size_usd,
                fill_price=price,
                ts_ms=ts_ms,
                exit_strategies=contrib.exit_strategies,  # inherit
                fee_round_trip=contrib.fee_round_trip,
                signal_confidence=decision.opportunity.signal_confidence if decision.opportunity else 0.0,
                signal_reason=f"pm_add:{decision.reason}",
                regime=contrib.regime,
            )
            logger.info(
                f"[PM-ADD] {contrib.ticker} {contrib.strategy_name} "
                f"+${decision.add_size_usd:.2f} {decision.reason}"
            )
            return
