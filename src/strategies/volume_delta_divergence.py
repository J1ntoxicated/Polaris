"""Volume Delta Divergence — pure (P6).

HYPOTHESIS-025: Cumulative delta (buy_vol - sell_vol) divergence from price.

Logic (`evaluate_delta(okx_tick, recent_trades, window_sec)`):
  Cumulative delta over last `window_sec` seconds from OKX trades WS.
    delta = Σ(buy_vol - sell_vol)
  Divergence signals:
  - Price DOWN (< -0.1%) + delta POSITIVE (bulls absorbing) → bullish divergence → ENTER_LONG
  - Price UP (> +0.1%) + delta NEGATIVE → bearish divergence → SPOT skip (HOLD)

  Note: SPOT-only (ADR-001). Bearish divergence (price up, delta neg) = distribution →
  could theoretically short but SPOT cannot. HOLD is correct.

Exit:
  - delta turns negative (selling pressure) → EXIT

Guards:
  - Fewer than min_trades → HOLD (insufficient sample)
  - Window has no price reference (no candle or tick open) → HOLD

Source: OKX trades WS (get_recent_trades — already active).
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_WINDOW_SEC: int = 60
DEFAULT_MIN_TRADES: int = 10
DEFAULT_PRICE_DOWN_THRESHOLD: float = -0.001  # -0.1%
DEFAULT_TARGET_SIZE_USD: float = 200.0
# Minimum absolute delta ratio vs total volume for signal confidence
DEFAULT_MIN_DELTA_RATIO: float = 0.10  # 10% net imbalance


class VolumeDeltaDivergence(Strategy):
    """Volume Delta Divergence — divergence between price and cumulative order flow.

    NOTE: standard `evaluate(window)` returns HOLD.
    Call `evaluate_delta(okx_tick, recent_trades, window_sec)`.

    recent_trades: list of (ts_ms, side, size, price) tuples from OKX trades WS.
    """

    name = "volume_delta_divergence"
    min_window = 1

    def __init__(
        self,
        window_sec: int = DEFAULT_WINDOW_SEC,
        min_trades: int = DEFAULT_MIN_TRADES,
        price_down_threshold: float = DEFAULT_PRICE_DOWN_THRESHOLD,
        min_delta_ratio: float = DEFAULT_MIN_DELTA_RATIO,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.window_sec = window_sec
        self.min_trades = min_trades
        self.price_down_threshold = price_down_threshold
        self.min_delta_ratio = min_delta_ratio
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="VolumeDeltaDivergence requires trades — use evaluate_delta",
        )

    def evaluate_delta(
        self,
        okx_tick: dict,
        recent_trades: list,
        window_sec: int | None = None,
    ) -> Signal:
        """Evaluate volume delta divergence signal.

        Args:
            okx_tick: OKX ticker dict with 'last', 'open24h', 'ts'.
            recent_trades: list of (ts_ms, side, size, price) from OKX WS.
            window_sec: rolling window in seconds (default self.window_sec).

        Pure function — no I/O.
        """
        ws = window_sec if window_sec is not None else self.window_sec
        ts = int(okx_tick.get("ts", 0) or 0) or 1
        last = float(okx_tick.get("last", 0) or 0)
        open24 = float(okx_tick.get("open24h", 0) or 0)

        if last <= 0:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason="invalid last price",
            )

        # Filter trades to window
        cutoff_ms = ts - ws * 1000
        window_trades = [t for t in recent_trades if t[0] >= cutoff_ms]

        if len(window_trades) < self.min_trades:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason=f"insufficient trades n={len(window_trades)} < {self.min_trades}",
            )

        # Compute cumulative delta
        buy_vol = sum(t[2] for t in window_trades if t[1] == "buy")
        sell_vol = sum(t[2] for t in window_trades if t[1] == "sell")
        total_vol = buy_vol + sell_vol
        if total_vol <= 0:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason="zero total volume",
            )

        delta = buy_vol - sell_vol
        delta_ratio = delta / total_vol  # positive = net buying, negative = net selling

        # Exit: net selling pressure
        if delta_ratio < -self.min_delta_ratio:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.65,
                reason=f"delta negative ratio={delta_ratio:+.3f} (selling pressure)",
            )

        # Price change reference — use open24h or first trade price
        price_ref = open24 if open24 > 0 else (window_trades[0][3] if window_trades else 0)
        if price_ref <= 0:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason="no price reference",
            )

        price_chg = (last - price_ref) / price_ref

        # Bullish divergence: price down + buyers absorbing (positive delta)
        if price_chg <= self.price_down_threshold and delta_ratio >= self.min_delta_ratio:
            confidence = min(0.85, 0.60 + delta_ratio * 0.5)
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=confidence,
                target_size_usd=self.target_size_usd,
                reason=(
                    f"bullish_divergence price={price_chg*100:+.2f}% "
                    f"delta_ratio={delta_ratio:+.3f} n={len(window_trades)}"
                ),
            )

        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"no_divergence price={price_chg*100:+.2f}% delta_ratio={delta_ratio:+.3f}",
        )
