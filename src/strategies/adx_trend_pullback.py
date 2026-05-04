"""ADX Trend Strength + RSI Pullback — pure (P6).

HYPOTHESIS-030: 1H ADX > 25 (strong uptrend) + +DI > -DI + RSI < 40 (pullback) → ENTER_LONG.

Logic (`evaluate(window)`):
  - ADX(14): Average Directional Index using Wilder smoothing.
    True Range → +DM / -DM → Wilder smooth → +DI / -DI → DX → ADX.
  - RSI(14): standard Wilder RSI.
  - Signal: ADX > adx_threshold (25) AND +DI > -DI AND RSI < rsi_pullback (40) → ENTER_LONG.
  - Exit: RSI > rsi_exit (60) OR ADX < adx_min_exit (20) → EXIT.

Edge: Strong uptrend confirmed by ADX + buying-exhaustion pullback (RSI<40)
      = high-probability continuation entry with trend momentum.

Tickers: BTC/ETH/SOL/DOGE/PEPE/SUI (1H candles).
Pure: no I/O, deterministic, side-effect free.
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_ADX_PERIOD: int = 14
DEFAULT_RSI_PERIOD: int = 14
DEFAULT_ADX_THRESHOLD: float = 25.0   # minimum ADX for strong trend
DEFAULT_ADX_MIN_EXIT: float = 20.0    # exit when trend weakens
DEFAULT_RSI_PULLBACK: float = 40.0    # oversold-ish pullback threshold
DEFAULT_RSI_EXIT: float = 60.0        # exit when momentum restored
DEFAULT_TARGET_SIZE_USD: float = 200.0


def compute_adx(
    candles: list[Candle],
    period: int = DEFAULT_ADX_PERIOD,
) -> tuple[float, float, float]:
    """Compute ADX, +DI, -DI using Wilder smoothing.

    Args:
        candles: time-ordered candle list (most recent last).
        period: ADX smoothing period (default 14).

    Returns:
        (adx, plus_di, minus_di) — values in [0, 100].
        Returns (0.0, 0.0, 0.0) when insufficient data.

    Pure function — no I/O, no side effects.
    """
    n = len(candles)
    if n < period * 2 + 1:
        return (0.0, 0.0, 0.0)

    true_ranges: list[float] = []
    plus_dms: list[float] = []
    minus_dms: list[float] = []

    for i in range(1, n):
        h = candles[i].high
        l = candles[i].low
        c = candles[i].close
        prev_c = candles[i - 1].close
        prev_h = candles[i - 1].high
        prev_l = candles[i - 1].low

        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        true_ranges.append(tr)

        up_move = h - prev_h
        down_move = prev_l - l
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    # Wilder smooth the first `period` bars
    def _wilder_init(vals: list[float]) -> float:
        return sum(vals[:period])

    atr = _wilder_init(true_ranges)
    plus_dm_s = _wilder_init(plus_dms)
    minus_dm_s = _wilder_init(minus_dms)

    dx_list: list[float] = []

    def _dx(plus: float, minus: float, tr: float) -> float:
        if tr < 1e-12:
            return 0.0
        p_di = (plus / tr) * 100.0
        m_di = (minus / tr) * 100.0
        if p_di + m_di < 1e-9:
            return 0.0
        return abs(p_di - m_di) / (p_di + m_di) * 100.0

    dx_list.append(_dx(plus_dm_s, minus_dm_s, atr))

    # Wilder smooth remaining bars
    for i in range(period, len(true_ranges)):
        atr = atr - atr / period + true_ranges[i]
        plus_dm_s = plus_dm_s - plus_dm_s / period + plus_dms[i]
        minus_dm_s = minus_dm_s - minus_dm_s / period + minus_dms[i]
        dx_list.append(_dx(plus_dm_s, minus_dm_s, atr))

    # ADX = smoothed DX
    if len(dx_list) < period:
        return (0.0, 0.0, 0.0)

    adx = sum(dx_list[:period]) / period
    for dx_val in dx_list[period:]:
        adx = (adx * (period - 1) + dx_val) / period

    # Final +DI / -DI from last Wilder smoothed values
    if atr < 1e-12:
        return (adx, 0.0, 0.0)
    plus_di = (plus_dm_s / atr) * 100.0
    minus_di = (minus_dm_s / atr) * 100.0

    return (adx, plus_di, minus_di)


def compute_rsi_scalar(closes: list[float], period: int = DEFAULT_RSI_PERIOD) -> float:
    """Wilder's RSI — scalar (last bar).

    Args:
        closes: time-ordered close list (period+1 minimum).
        period: RSI period (default 14).

    Returns:
        RSI value in [0, 100]. Returns 50.0 when insufficient data.

    Pure function — no I/O.
    """
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class ADXTrendPullback(Strategy):
    """ADX Trend Strength + RSI Pullback — 1H strong trend pullback entry.

    HYPO-030: ADX > 25 + +DI > -DI (uptrend) + RSI < 40 (pullback) → ENTER_LONG.
              RSI > 60 OR ADX < 20 → EXIT.

    Uses standard `evaluate(window)` — 1H candle list.
    """

    name = "adx_trend_pullback"

    def __init__(
        self,
        adx_period: int = DEFAULT_ADX_PERIOD,
        rsi_period: int = DEFAULT_RSI_PERIOD,
        adx_threshold: float = DEFAULT_ADX_THRESHOLD,
        adx_min_exit: float = DEFAULT_ADX_MIN_EXIT,
        rsi_pullback: float = DEFAULT_RSI_PULLBACK,
        rsi_exit: float = DEFAULT_RSI_EXIT,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.adx_period = adx_period
        self.rsi_period = rsi_period
        self.adx_threshold = adx_threshold
        self.adx_min_exit = adx_min_exit
        self.rsi_pullback = rsi_pullback
        self.rsi_exit = rsi_exit
        self.target_size_usd = target_size_usd
        # ADX needs period*2+1 bars minimum; RSI needs period+1; take the larger
        self.min_window = max(adx_period * 2 + 1, rsi_period + 1)

    def evaluate(self, window: list[Candle]) -> Signal:
        """Evaluate ADX + RSI pullback signal.

        Args:
            window: time-ordered Candle list (most recent last).

        Returns:
            Signal — ENTER_LONG / EXIT / HOLD.
        """
        self._ensure_window(window)
        closes = [c.close for c in window]
        ts = window[-1].timestamp_ms

        adx, plus_di, minus_di = compute_adx(window, self.adx_period)
        rsi = compute_rsi_scalar(closes, self.rsi_period)

        # Exit conditions first (protect open position)
        if rsi > self.rsi_exit or adx < self.adx_min_exit:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.65,
                reason=f"exit adx={adx:.1f} rsi={rsi:.1f}",
            )

        # Entry: strong uptrend + pullback
        if (adx > self.adx_threshold
                and plus_di > minus_di
                and rsi < self.rsi_pullback):
            confidence = min(0.85, 0.60 + (adx - self.adx_threshold) / 100.0)
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=confidence,
                target_size_usd=self.target_size_usd,
                reason=(
                    f"adx_pullback adx={adx:.1f} +DI={plus_di:.1f} "
                    f"-DI={minus_di:.1f} rsi={rsi:.1f}"
                ),
            )

        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"no_signal adx={adx:.1f} rsi={rsi:.1f} +DI={plus_di:.1f}",
        )
