"""Carry Trade — Funding Rate Extreme (HYPO-036).

Academic basis:
  Hong Liu & Lu Yu (2024). "Funding Rates and Cryptocurrency Returns".
  - Extreme negative funding predicts SPOT long positive returns.
  - CoinGlass empirical: funding <= -0.05% (8h) precedes rally with ~70% hit rate.

Distinction from HYPO-027 FundingRateFilter:
  - HYPO-027: size modifier for other strategies (not standalone entry).
  - HYPO-036: STANDALONE entry strategy triggered by funding alone.
    No candle momentum required. Entry is the carry opportunity itself.

Logic:
  1. funding_8h <= entry_threshold (default -0.05%) → ENTER_LONG (shorts squeezed)
  2. Once in trade: exit when funding recovers >= exit_threshold (default 0.0%)
     OR max_hold_hours elapsed (default 12h = 3 × 4h cycles).
  3. Strictly SPOT-only (never SHORT).

Strategy type: event-driven (trigger on funding poll, not candle close).
Primary timeframe: every 8h (funding payment cycle).

Implementation interface:
  evaluate_funding(funding_8h, position_age_hours) → Signal.
  evaluate(window) → HOLD (candle-based evaluate not used; satisfies ABC).

Pure function — no I/O, no module state, deterministic.
P6 classification: pure=true
"""
from __future__ import annotations

from typing import Optional

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_ENTRY_THRESHOLD: float = -0.0005   # -0.05% (8h) — shorts squeezed
DEFAULT_EXIT_THRESHOLD: float = 0.0        # funding recovered → release carry
DEFAULT_MAX_HOLD_HOURS: float = 12.0       # max hold 12h (3 × 4h cycles)
DEFAULT_TARGET_SIZE_USD: float = 200.0
DEFAULT_CONFIDENCE_BASE: float = 0.65      # Liu & Yu 2024: ~70% hit rate empirical


class FundingCarry(Strategy):
    """Carry Trade triggered by extreme negative funding rate.

    Standalone entry strategy (HYPO-036). Separate from FundingRateFilter (HYPO-027).

    evaluate_funding(funding_8h, position_age_hours) is the main interface.
    evaluate(window) returns HOLD (satisfies Strategy ABC).

    Attributes:
        entry_threshold: funding_8h at or below which to enter (default -0.05%).
        exit_threshold: funding_8h at or above which to exit carry (default 0.0%).
        max_hold_hours: force-exit after this many hours regardless of funding.
        target_size_usd: base position size.
    """

    name = "funding_carry"
    min_window = 1  # not candle-based; satisfies Strategy ABC

    def __init__(
        self,
        entry_threshold: float = DEFAULT_ENTRY_THRESHOLD,
        exit_threshold: float = DEFAULT_EXIT_THRESHOLD,
        max_hold_hours: float = DEFAULT_MAX_HOLD_HOURS,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.max_hold_hours = max_hold_hours
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        """Candle-based evaluate — not used for FundingCarry (funding-triggered).

        Returns HOLD always (shell must call evaluate_funding directly).
        """
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="funding_carry_use_evaluate_funding",
        )

    def evaluate_funding(
        self,
        funding_8h: Optional[float],
        ts_ms: int,
        position_age_hours: float = 0.0,
        in_position: bool = False,
    ) -> Signal:
        """Evaluate funding carry signal.

        Args:
            funding_8h: current Binance 8h funding rate as decimal.
                        None → data unavailable → HOLD.
            ts_ms: current timestamp in milliseconds (for Signal).
            position_age_hours: how long current position has been open (0 if no position).
            in_position: True if currently holding a carry position.

        Returns:
            Signal:
              - ENTER_LONG: funding <= entry_threshold AND not in position.
              - EXIT: in_position AND (funding >= exit_threshold OR max_hold exceeded).
              - HOLD: otherwise.

        Pure function — no I/O.
        """
        if ts_ms <= 0:
            ts_ms = 1

        # Guard: no funding data
        if funding_8h is None:
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason="funding_carry_hold no_funding_data",
            )

        # If in position: check exit conditions
        if in_position:
            max_hold_exceeded = position_age_hours >= self.max_hold_hours
            funding_recovered = funding_8h >= self.exit_threshold

            if max_hold_exceeded:
                return Signal(
                    timestamp_ms=ts_ms,
                    action=SignalAction.EXIT,
                    confidence=0.9,
                    reason=(
                        f"funding_carry_exit max_hold={position_age_hours:.1f}h "
                        f">= {self.max_hold_hours:.0f}h"
                    ),
                )

            if funding_recovered:
                return Signal(
                    timestamp_ms=ts_ms,
                    action=SignalAction.EXIT,
                    confidence=0.8,
                    reason=(
                        f"funding_carry_exit funding_recovered={funding_8h*100:+.4f}% "
                        f">= {self.exit_threshold*100:+.2f}%"
                    ),
                )

            # Still in carry — HOLD
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=(
                    f"funding_carry_hold_in_position funding={funding_8h*100:+.4f}% "
                    f"age={position_age_hours:.1f}h"
                ),
            )

        # Not in position: check entry
        if funding_8h <= self.entry_threshold:
            # Confidence scales with funding extremity
            extremity = abs(funding_8h / self.entry_threshold)  # >= 1.0 when at/below threshold
            confidence = min(0.95, DEFAULT_CONFIDENCE_BASE + (extremity - 1.0) * 0.10)
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.ENTER_LONG,
                confidence=round(confidence, 3),
                target_size_usd=self.target_size_usd,
                reason=(
                    f"funding_carry_enter funding={funding_8h*100:+.4f}% "
                    f"<= threshold={self.entry_threshold*100:+.2f}% "
                    f"extremity={extremity:.2f}x"
                ),
            )

        return Signal(
            timestamp_ms=ts_ms,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=(
                f"funding_carry_hold funding={funding_8h*100:+.4f}% "
                f"above_threshold={self.entry_threshold*100:+.2f}%"
            ),
        )
