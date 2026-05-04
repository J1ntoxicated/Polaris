"""Stochastic RSI Mean Reversion — pure (P6).

HYPOTHESIS-029: 1h Stoch RSI (K, D) 14/3/3 oversold crossover → mean reversion long.

Logic (`evaluate(window)`):
  - Compute RSI(14) on 1H closes.
  - Compute Stoch RSI: K = Stochastic of RSI over stoch_period (default 14).
    K_raw = (RSI - min_RSI_N) / (max_RSI_N - min_RSI_N) * 100
  - Smooth K with smooth_k SMA (default 3) → %K.
  - Smooth %K with smooth_d SMA (default 3) → %D.
  - K < oversold (20) AND K crosses up D → ENTER_LONG (oversold reversal).
  - K > overbought (80) AND K crosses down D → EXIT.

Tickers: BTC/ETH/SOL/DOGE/PEPE/SUI (1H candles).
Auto-deprecate: n=10, win<40% trigger (realtime_runner auto_deprecate).

Pure: no I/O, deterministic, side-effect free.
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_RSI_PERIOD: int = 14
DEFAULT_STOCH_PERIOD: int = 14
DEFAULT_SMOOTH_K: int = 3
DEFAULT_SMOOTH_D: int = 3
DEFAULT_OVERSOLD: float = 20.0
DEFAULT_OVERBOUGHT: float = 80.0
DEFAULT_TARGET_SIZE_USD: float = 200.0


def _compute_rsi_series(closes: list[float], period: int) -> list[float]:
    """Compute RSI series (Wilder smoothing) for each bar.

    Returns list of RSI values, same length as closes.
    First `period` values are None (insufficient data) → filled with 50.0.

    Pure function — no I/O, no side effects.
    """
    n = len(closes)
    rsi_series: list[float] = [50.0] * n
    if n < period + 1:
        return rsi_series

    # Initial averages from first period+1 bars
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        rsi_series[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_series[period] = 100.0 - (100.0 / (1.0 + rs))

    # Wilder smoothing for remaining bars
    for i in range(period + 1, n):
        diff = closes[i] - closes[i - 1]
        g = max(diff, 0.0)
        lo = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + lo) / period
        if avg_loss == 0:
            rsi_series[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_series[i] = 100.0 - (100.0 / (1.0 + rs))

    return rsi_series


def _sma(values: list[float], period: int) -> list[float]:
    """Simple moving average. Returns same-length list; first period-1 values = first value.

    Pure function — no I/O.
    """
    n = len(values)
    result: list[float] = []
    for i in range(n):
        if i < period - 1:
            result.append(values[0])  # pad with first value
        else:
            result.append(sum(values[i - period + 1 : i + 1]) / period)
    return result


def compute_stoch_rsi(
    closes: list[float],
    rsi_period: int = DEFAULT_RSI_PERIOD,
    stoch_period: int = DEFAULT_STOCH_PERIOD,
    smooth_k: int = DEFAULT_SMOOTH_K,
    smooth_d: int = DEFAULT_SMOOTH_D,
) -> tuple[float, float]:
    """Compute latest Stochastic RSI %K and %D values.

    Args:
        closes: time-ordered close prices (most recent last).
        rsi_period: RSI lookback period (default 14).
        stoch_period: Stochastic lookback over RSI series (default 14).
        smooth_k: %K smoothing period (default 3).
        smooth_d: %D smoothing period (default 3).

    Returns:
        (k, d): %K and %D values in [0, 100].
        Returns (50.0, 50.0) when insufficient data.

    Pure function — no I/O, no side effects.
    """
    min_required = rsi_period + stoch_period + smooth_k + smooth_d
    if len(closes) < min_required:
        return (50.0, 50.0)

    rsi_vals = _compute_rsi_series(closes, rsi_period)

    # Stochastic of RSI — rolling min/max over stoch_period
    n = len(rsi_vals)
    k_raw: list[float] = []
    for i in range(n):
        if i < stoch_period - 1:
            k_raw.append(50.0)  # pad
        else:
            window = rsi_vals[i - stoch_period + 1 : i + 1]
            mn = min(window)
            mx = max(window)
            if mx - mn < 1e-9:
                k_raw.append(50.0)
            else:
                k_raw.append((rsi_vals[i] - mn) / (mx - mn) * 100.0)

    # Smooth K and D
    k_smooth = _sma(k_raw, smooth_k)
    d_smooth = _sma(k_smooth, smooth_d)

    return (k_smooth[-1], d_smooth[-1])


def _prev_k_d(
    closes: list[float],
    rsi_period: int,
    stoch_period: int,
    smooth_k: int,
    smooth_d: int,
) -> tuple[float, float]:
    """Compute K, D for the bar before the last bar (crossover detection).

    Returns (50.0, 50.0) when not enough data.

    Pure function — no I/O.
    """
    if len(closes) < 2:
        return (50.0, 50.0)
    return compute_stoch_rsi(closes[:-1], rsi_period, stoch_period, smooth_k, smooth_d)


class StochRSI(Strategy):
    """Stochastic RSI Mean Reversion — 1H oversold crossover.

    HYPO-029: K < 20 AND crossover up D → ENTER_LONG.
              K > 80 AND crossover down D → EXIT.

    Uses standard `evaluate(window)` — 1H candle list.
    """

    name = "stoch_rsi"

    def __init__(
        self,
        rsi_period: int = DEFAULT_RSI_PERIOD,
        stoch_period: int = DEFAULT_STOCH_PERIOD,
        smooth_k: int = DEFAULT_SMOOTH_K,
        smooth_d: int = DEFAULT_SMOOTH_D,
        oversold: float = DEFAULT_OVERSOLD,
        overbought: float = DEFAULT_OVERBOUGHT,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.rsi_period = rsi_period
        self.stoch_period = stoch_period
        self.smooth_k = smooth_k
        self.smooth_d = smooth_d
        self.oversold = oversold
        self.overbought = overbought
        self.target_size_usd = target_size_usd
        # Minimum candles: rsi_period + stoch_period + smooth_k + smooth_d + 1 (prev bar)
        self.min_window = rsi_period + stoch_period + smooth_k + smooth_d + 1

    def evaluate(self, window: list[Candle]) -> Signal:
        """Evaluate Stochastic RSI signal from candle window.

        Args:
            window: time-ordered Candle list (most recent last).

        Returns:
            Signal — ENTER_LONG (oversold crossover up) / EXIT (overbought crossover down) / HOLD.
        """
        self._ensure_window(window)
        closes = [c.close for c in window]
        ts = window[-1].timestamp_ms

        k_now, d_now = compute_stoch_rsi(
            closes, self.rsi_period, self.stoch_period, self.smooth_k, self.smooth_d
        )
        k_prev, d_prev = _prev_k_d(
            closes, self.rsi_period, self.stoch_period, self.smooth_k, self.smooth_d
        )

        # Oversold crossover up: K < oversold AND K crosses above D
        if k_now < self.oversold and k_prev <= d_prev and k_now > d_now:
            confidence = min(0.85, 0.60 + (self.oversold - k_now) / self.oversold * 0.5)
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=confidence,
                target_size_usd=self.target_size_usd,
                reason=f"stoch_rsi oversold crossover K={k_now:.1f} D={d_now:.1f}",
            )

        # Overbought crossover down: K > overbought AND K crosses below D
        if k_now > self.overbought and k_prev >= d_prev and k_now < d_now:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.70,
                reason=f"stoch_rsi overbought crossdown K={k_now:.1f} D={d_now:.1f}",
            )

        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"stoch_rsi no_signal K={k_now:.1f} D={d_now:.1f}",
        )
