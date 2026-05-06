"""OpportunityScanner — periodic cross-strategy entry signal scan (Phase 23.2).

Every N seconds (default 30s), evaluate ALL (strategy, ticker) combos for
entry signals. Cache the top opportunities ranked by signal_quality × historical_ev.

Used by ReallocationDecider to compare against current open positions:
"is there a better use of this capital right now?"

Pure-ish: scanner takes pre-built signal evaluator callable + opportunities
list. Caller (runner) provides the eval function. No I/O directly.

Codex round-1 fix: scan is periodic, not per-tick. Per-tick scan would
overwhelm CPU + cause churn on noisy 1-tick signals.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Opportunity:
    """A potential entry — discovered by OpportunityScanner."""
    ticker: str
    strategy_name: str
    hypo_id: str
    signal_confidence: float       # [0, 1] from strategy
    historical_ev_pct: float       # backtest / paper EV per trade for this strategy
    expected_return_pct: float     # signal_confidence × historical_ev (rank key)
    signal_reason: str
    ts_ms: int
    calibrated_confidence: float = 0.0   # F5: from bayesian.calibrate_confidence(); 0.0 = not set


@dataclass(frozen=True)
class ScanResult:
    """Output of one scan cycle."""
    ts_ms: int
    opportunities: tuple[Opportunity, ...]    # sorted desc by expected_return_pct
    n_evaluated: int
    n_signals: int


class OpportunityScanner:
    """Stateful — caches latest scan + scan interval throttle."""

    def __init__(
        self,
        scan_interval_s: float = 30.0,
        top_n: int = 10,
    ) -> None:
        self.scan_interval_s = scan_interval_s
        self.top_n = top_n
        self._last_scan_ms: int = 0
        self._cached_result: Optional[ScanResult] = None

    @property
    def cached(self) -> Optional[ScanResult]:
        return self._cached_result

    def should_rescan(self, ts_ms: int) -> bool:
        # First-ever scan (no prior) always allowed
        if self._last_scan_ms == 0:
            return True
        return (ts_ms - self._last_scan_ms) >= int(self.scan_interval_s * 1000)

    def scan(
        self,
        ts_ms: int,
        candidates: list[tuple[str, str, str]],
        signal_fn: Callable[[str, str, str], Optional[Opportunity]],
        force: bool = False,
    ) -> ScanResult:
        """Run a scan cycle.

        candidates: [(ticker, strategy_name, hypo_id), ...] — caller-provided list.
        signal_fn(ticker, strategy_name, hypo_id) → Opportunity or None.
                  Returns None if strategy doesn't fire.

        Caller is responsible for actually evaluating the strategy. Scanner
        just orchestrates + ranks.
        """
        if not force and not self.should_rescan(ts_ms):
            return self._cached_result or ScanResult(
                ts_ms=ts_ms, opportunities=(), n_evaluated=0, n_signals=0,
            )

        opportunities: list[Opportunity] = []
        for (ticker, strategy_name, hypo_id) in candidates:
            try:
                opp = signal_fn(ticker, strategy_name, hypo_id)
                if opp is not None:
                    opportunities.append(opp)
            except Exception as e:
                logger.warning(
                    f"[SCAN] eval error {ticker}/{strategy_name}: {e!r}"
                )
        # Rank by expected_return_pct desc
        opportunities.sort(key=lambda o: o.expected_return_pct, reverse=True)
        top = tuple(opportunities[: self.top_n])

        result = ScanResult(
            ts_ms=ts_ms,
            opportunities=top,
            n_evaluated=len(candidates),
            n_signals=len(opportunities),
        )
        self._cached_result = result
        self._last_scan_ms = ts_ms
        if top:
            logger.info(
                f"[SCAN] {result.n_signals}/{result.n_evaluated} signals, top: "
                f"{top[0].ticker}/{top[0].strategy_name} "
                f"er={top[0].expected_return_pct*100:+.2f}%"
            )
        return result

    def best_for_ticker(self, ticker: str) -> Optional[Opportunity]:
        """Top opportunity for a specific ticker (or None)."""
        if self._cached_result is None:
            return None
        for o in self._cached_result.opportunities:
            if o.ticker == ticker:
                return o
        return None

    def best_overall(self) -> Optional[Opportunity]:
        """#1 ranked opportunity across portfolio (or None)."""
        if self._cached_result is None or not self._cached_result.opportunities:
            return None
        return self._cached_result.opportunities[0]
