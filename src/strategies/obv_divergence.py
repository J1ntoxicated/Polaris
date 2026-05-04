"""OBV (On-Balance Volume) Divergence — pure (P6).

HYPOTHESIS-031: 1H cumulative OBV vs price divergence → bullish signal.

Logic (`evaluate(window)`):
  - OBV: cumulative volume indicator.
    OBV[i] = OBV[i-1] + vol[i]  if close[i] > close[i-1]
    OBV[i] = OBV[i-1] - vol[i]  if close[i] < close[i-1]
    OBV[i] = OBV[i-1]           if close[i] == close[i-1]
  - Divergence window: compare last `lookback` bars.
    Bullish: price DOWN (lower close than lookback bars ago) + OBV UP → ENTER_LONG.
    Bearish: price UP + OBV DOWN → HOLD (SPOT cannot short).
  - Exit: OBV trend reverses (OBV now lower than obv_exit_lookback bars ago) → EXIT.

Guards:
  - Fewer than min_window candles → ValueError (Strategy contract).
  - Price change < min_price_change_pct threshold → HOLD (noise filter).

Tickers: BTC/ETH/SOL/DOGE/PEPE/SUI (1H candles).
Pure: no I/O, deterministic, side-effect free.
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_LOOKBACK: int = 10           # bars to compare for divergence
DEFAULT_OBV_EXIT_LOOKBACK: int = 3   # bars to compare for OBV reversal exit
DEFAULT_MIN_PRICE_CHANGE_PCT: float = 0.005  # 0.5% minimum price move for divergence
DEFAULT_MIN_OBV_CHANGE_PCT: float = 0.02     # 2% minimum OBV change (vs |OBV range| normalised)
DEFAULT_TARGET_SIZE_USD: float = 200.0


def compute_obv(candles: list[Candle]) -> list[float]:
    """Compute OBV series from candle list.

    Args:
        candles: time-ordered Candle list (most recent last).

    Returns:
        List of OBV values same length as candles.
        First value = 0.0 (reference).

    Pure function — no I/O, no side effects.
    """
    obv_series: list[float] = [0.0]
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        curr_close = candles[i].close
        vol = candles[i].volume
        if curr_close > prev_close:
            obv_series.append(obv_series[-1] + vol)
        elif curr_close < prev_close:
            obv_series.append(obv_series[-1] - vol)
        else:
            obv_series.append(obv_series[-1])
    return obv_series


class OBVDivergence(Strategy):
    """OBV Divergence — price vs volume accumulation divergence.

    HYPO-031: price DOWN + OBV UP (bullish divergence) → ENTER_LONG.
              OBV reversal → EXIT.

    Uses standard `evaluate(window)` — 1H candle list.
    """

    name = "obv_divergence"

    def __init__(
        self,
        lookback: int = DEFAULT_LOOKBACK,
        obv_exit_lookback: int = DEFAULT_OBV_EXIT_LOOKBACK,
        min_price_change_pct: float = DEFAULT_MIN_PRICE_CHANGE_PCT,
        min_obv_change_pct: float = DEFAULT_MIN_OBV_CHANGE_PCT,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.lookback = lookback
        self.obv_exit_lookback = obv_exit_lookback
        self.min_price_change_pct = min_price_change_pct
        self.min_obv_change_pct = min_obv_change_pct
        self.target_size_usd = target_size_usd
        self.min_window = lookback + 1

    def evaluate(self, window: list[Candle]) -> Signal:
        """Evaluate OBV divergence signal.

        Args:
            window: time-ordered Candle list (most recent last).

        Returns:
            Signal — ENTER_LONG (bullish divergence) / EXIT (OBV reversal) / HOLD.
        """
        self._ensure_window(window)
        ts = window[-1].timestamp_ms

        obv = compute_obv(window)

        # Current vs lookback-bars-ago comparison
        current_close = window[-1].close
        past_close = window[-1 - self.lookback].close
        current_obv = obv[-1]
        past_obv = obv[-1 - self.lookback]

        if past_close <= 0:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason="invalid past close price",
            )

        price_chg = (current_close - past_close) / past_close

        # Normalise OBV change — use total volume as denominator proxy
        total_vol = sum(abs(obv[i] - obv[i - 1]) for i in range(1, len(obv)))
        if total_vol < 1e-9:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason="zero volume in OBV window",
            )

        obv_chg_norm = (current_obv - past_obv) / total_vol  # normalised OBV change

        # Exit: OBV reversal — OBV dropping over short lookback
        if len(obv) >= self.obv_exit_lookback + 1:
            obv_short_ago = obv[-1 - self.obv_exit_lookback]
            obv_short_chg = (current_obv - obv_short_ago) / (total_vol + 1e-9)
            if obv_short_chg < -self.min_obv_change_pct:
                return Signal(
                    timestamp_ms=ts,
                    action=SignalAction.EXIT,
                    confidence=0.65,
                    reason=(
                        f"obv_reversal obv_short_chg={obv_short_chg*100:+.2f}% "
                        f"(last {self.obv_exit_lookback}bars)"
                    ),
                )

        # Bullish divergence: price DOWN + OBV UP
        if (price_chg <= -self.min_price_change_pct
                and obv_chg_norm >= self.min_obv_change_pct):
            confidence = min(0.82, 0.58 + abs(price_chg) * 2.0)
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=confidence,
                target_size_usd=self.target_size_usd,
                reason=(
                    f"obv_bullish_div price={price_chg*100:+.2f}% "
                    f"obv_norm={obv_chg_norm*100:+.2f}% "
                    f"lookback={self.lookback}bars"
                ),
            )

        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=(
                f"no_divergence price={price_chg*100:+.2f}% "
                f"obv_norm={obv_chg_norm*100:+.2f}%"
            ),
        )
