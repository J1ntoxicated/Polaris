"""Time-Series Momentum — Moskowitz/Ooi/Pedersen 2012 JFE.

HYPOTHESIS-032: Multi-timeframe return signal ensemble.

Academic basis:
  Moskowitz, T.J., Ooi, Y.H., Pedersen, L.H. (2012).
  "Time series momentum". Journal of Financial Economics, 104(2), 228-250.
  - Tested on 58 futures markets, Sharpe 1.0+, persistent over 25 years.
  - Hypothesis: past 1-12 month return predicts future return continuation.

Crypto adaptation:
  - Lookbacks mapped to daily candle units: 1d / 7d / 30d.
  - SPOT-only: short signals → SKIP (never ENTER_SHORT).
  - Signal: ratio of positive-return lookbacks.
    ratio >= enter_threshold → ENTER_LONG
    ratio <= exit_threshold → EXIT
    else → HOLD

Logic:
  For each lookback L in lookbacks_days:
    signal_L = 1 if candles[-1].close > candles[-(L+1)].close else 0
  ratio = sum(signals) / len(signals)
  if ratio >= enter_threshold: ENTER_LONG
  if ratio <= exit_threshold: EXIT
  else: HOLD

Pure function — no I/O, no module state, deterministic.
P6 classification: pure=true
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_LOOKBACKS_DAYS: tuple[int, ...] = (1, 7, 30)
DEFAULT_ENTER_THRESHOLD: float = 0.6
DEFAULT_EXIT_THRESHOLD: float = 0.4
DEFAULT_TARGET_SIZE_USD: float = 200.0


class TSMOM(Strategy):
    """Time-Series Momentum — Moskowitz/Ooi/Pedersen 2012 JFE.

    Multi-lookback return signal: ratio of positive-return lookbacks.
    SPOT-only (no SHORT).

    Attributes:
        lookbacks_days: tuple of day-count lookbacks to check.
        enter_threshold: fraction of lookbacks that must be positive for ENTER_LONG.
        exit_threshold: fraction at or below which EXIT is triggered.
        target_size_usd: base position size (dynamic_sizing may override).
    """

    name = "tsmom"

    def __init__(
        self,
        lookbacks_days: tuple[int, ...] = DEFAULT_LOOKBACKS_DAYS,
        enter_threshold: float = DEFAULT_ENTER_THRESHOLD,
        exit_threshold: float = DEFAULT_EXIT_THRESHOLD,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.lookbacks_days = lookbacks_days
        self.enter_threshold = enter_threshold
        self.exit_threshold = exit_threshold
        self.target_size_usd = target_size_usd
        # Need at least max(lookback)+1 candles
        self.min_window = max(lookbacks_days) + 1 if lookbacks_days else 2

    def evaluate(self, window: list[Candle]) -> Signal:
        """Evaluate TSMOM signal from candle window.

        Args:
            window: list of Candle objects (daily bars), most recent last.
                    Need >= max(lookbacks_days)+1 candles.

        Returns:
            Signal with action ENTER_LONG / EXIT / HOLD.
            HOLD on insufficient data (warmup guard).

        Pure — no I/O.
        """
        if len(window) < self.min_window or not window:
            ts = window[-1].timestamp_ms if window else 1
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason="tsmom_warmup insufficient_candles",
            )

        ts = window[-1].timestamp_ms
        current_close = window[-1].close

        signals = []
        for lookback in self.lookbacks_days:
            if len(window) > lookback:
                past_close = window[-(lookback + 1)].close
                signals.append(1 if current_close > past_close else 0)
            # If not enough candles for this specific lookback, skip it silently

        if not signals:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason="tsmom_no_valid_lookbacks",
            )

        ratio = sum(signals) / len(signals)

        if ratio >= self.enter_threshold:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=round(ratio, 3),
                target_size_usd=self.target_size_usd,
                reason=f"tsmom_enter ratio={ratio:.2f} lookbacks={self.lookbacks_days}",
            )

        if ratio <= self.exit_threshold:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=round(1.0 - ratio, 3),
                reason=f"tsmom_exit ratio={ratio:.2f} lookbacks={self.lookbacks_days}",
            )

        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"tsmom_hold ratio={ratio:.2f} deadzone [{self.exit_threshold},{self.enter_threshold}]",
        )
