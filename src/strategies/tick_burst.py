"""Tick Burst Follow — pure (P6).

HYPOTHESIS-028: OKX tickers WS — rapid price spike in 5s = momentum follow.

Logic (`evaluate_tick(tick, price_5s_ago)`):
  burst_pct = (current_price - price_5s_ago) / price_5s_ago
  burst_pct >= +0.3% → same-direction entry (ENTER_LONG), 60s holding.

Math: fee = 0.14% × 2 = 0.28% round-trip < 0.30% burst → positive EV (marginal).
  Real edge: momentum continuation after burst (additional ~0.1-0.5% follow-through).

Exit:
  burst_pct <= -exit_threshold → price gave back gains → EXIT

Guards:
  - price_5s_ago None or <= 0 → HOLD (insufficient history)
  - burst_pct > max_burst_pct (5%) → likely bad tick / data anomaly → HOLD
  - Spread > max_spread_bps (50) → low liquidity → HOLD

Source: OKX tickers WS (tick payload), price_5s_ago from shell-maintained 5s history.
"""
from __future__ import annotations

from typing import Optional

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_BURST_THRESHOLD: float = 0.003    # +0.3% minimum spike
DEFAULT_EXIT_THRESHOLD: float = 0.001    # -0.1% revert → EXIT
DEFAULT_MAX_BURST_PCT: float = 0.05      # >5% = likely bad tick
DEFAULT_MAX_SPREAD_BPS: float = 50.0
DEFAULT_TARGET_SIZE_USD: float = 200.0


class TickBurst(Strategy):
    """5-second price burst momentum follow.

    NOTE: standard `evaluate(window)` returns HOLD.
    Call `evaluate_tick(tick, price_5s_ago)`.

    price_5s_ago: float — price 5 seconds ago (maintained by shell).
    """

    name = "tick_burst"
    min_window = 1

    def __init__(
        self,
        burst_threshold: float = DEFAULT_BURST_THRESHOLD,
        exit_threshold: float = DEFAULT_EXIT_THRESHOLD,
        max_burst_pct: float = DEFAULT_MAX_BURST_PCT,
        max_spread_bps: float = DEFAULT_MAX_SPREAD_BPS,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.burst_threshold = burst_threshold
        self.exit_threshold = exit_threshold
        self.max_burst_pct = max_burst_pct
        self.max_spread_bps = max_spread_bps
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="TickBurst requires price_5s_ago — use evaluate_tick",
        )

    def evaluate_tick(self, tick: dict, price_5s_ago: Optional[float]) -> Signal:
        """Evaluate tick burst momentum.

        Args:
            tick: OKX ticker dict with 'last', 'bid', 'ask', 'ts'.
            price_5s_ago: price 5 seconds ago (None if no history yet).

        Pure function — no I/O, deterministic.
        """
        ts = int(tick.get("ts", 0) or 0) or 1
        last = float(tick.get("last", 0) or 0)
        bid = float(tick.get("bid", 0) or 0)
        ask = float(tick.get("ask", 0) or 0)

        if last <= 0:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason="invalid last price",
            )

        if price_5s_ago is None or price_5s_ago <= 0:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason="no 5s price history",
            )

        burst_pct = (last - price_5s_ago) / price_5s_ago

        # Sanity: bad tick guard
        if abs(burst_pct) > self.max_burst_pct:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason=f"burst {burst_pct*100:+.2f}% > max {self.max_burst_pct*100:.0f}% (bad tick guard)",
            )

        # Exit: price gave back gains (reversal after burst)
        if burst_pct <= -self.exit_threshold:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.65,
                reason=f"burst reversal {burst_pct*100:+.2f}% <= -{self.exit_threshold*100:.1f}%",
            )

        # Spread filter
        if bid > 0 and ask > 0 and last > 0:
            spread_bps = (ask - bid) / last * 10_000
            if spread_bps > self.max_spread_bps:
                return Signal(
                    timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                    reason=f"spread {spread_bps:.1f}bps > {self.max_spread_bps}bps",
                )

        # Entry: burst upward
        if burst_pct >= self.burst_threshold:
            confidence = min(0.85, 0.65 + burst_pct / 0.01 * 0.05)
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=confidence,
                target_size_usd=self.target_size_usd,
                reason=f"tick_burst +{burst_pct*100:.2f}% in 5s",
            )

        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"no burst {burst_pct*100:+.2f}%",
        )
