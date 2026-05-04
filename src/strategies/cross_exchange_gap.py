"""Cross-Exchange Price Gap — pure (P6).

HYPOTHESIS-024: Binance BTC spot price leads OKX BTC by 0.1-0.5s.
Direct price gap (not taker flow ratio — cf. HYPO-014 BinanceLeadSignal).

Logic (`evaluate_cross(okx_tick, binance_price_state)`):
  gap_bps = (binance_price - okx_price) / okx_price * 10000
  - gap_bps >= +3.0 (Binance > OKX by 0.03%) → OKX under-priced → ENTER_LONG
  - gap_bps <= -1.0 → OKX above Binance → EXIT

Guards:
  - Binance price stale (> stale_ms) → HOLD
  - |gap_bps| > max_gap_bps (100) → data anomaly → HOLD

Design: HYPO-014 used taker_buy_ratio (flow); HYPO-024 uses raw price gap.
Source: okx_ws tickers + binance_ws @bookTicker (get_book_ticker).
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

# Minimum gap for entry (bps)
DEFAULT_ENTRY_GAP_BPS: float = 3.0
# Exit when OKX price is above Binance by this much (bps)
DEFAULT_EXIT_GAP_BPS: float = -1.0
# Sanity cap — gaps wider than this are likely data errors
DEFAULT_MAX_GAP_BPS: float = 100.0
# Binance price freshness — stale if older than this ms
DEFAULT_STALE_MS: int = 3_000
DEFAULT_TARGET_SIZE_USD: float = 200.0


class CrossExchangeGap(Strategy):
    """Cross-exchange direct price gap signal.

    NOTE: standard `evaluate(window)` returns HOLD.
    Call `evaluate_cross(okx_tick, binance_price_state)`.

    binance_price_state keys:
        price: float        — Binance best bid (or last price)
        ts_ms: int          — timestamp of Binance price
        now_ms: int         — local clock (avoids cross-exchange clock skew)
    """

    name = "cross_exchange_gap"
    min_window = 1

    def __init__(
        self,
        entry_gap_bps: float = DEFAULT_ENTRY_GAP_BPS,
        exit_gap_bps: float = DEFAULT_EXIT_GAP_BPS,
        max_gap_bps: float = DEFAULT_MAX_GAP_BPS,
        stale_ms: int = DEFAULT_STALE_MS,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.entry_gap_bps = entry_gap_bps
        self.exit_gap_bps = exit_gap_bps
        self.max_gap_bps = max_gap_bps
        self.stale_ms = stale_ms
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="CrossExchangeGap requires cross — use evaluate_cross",
        )

    def evaluate_cross(self, okx_tick: dict, binance_price_state: dict) -> Signal:
        """Evaluate cross-exchange price gap.

        Args:
            okx_tick: OKX ticker dict with 'last' and 'ts'.
            binance_price_state: dict with keys price, ts_ms, now_ms.

        Pure function — no I/O.
        """
        ts = int(okx_tick.get("ts", 0) or 0) or 1
        okx_price = float(okx_tick.get("last", 0) or 0)

        b_price = float(binance_price_state.get("price", 0) or 0)
        b_ts = int(binance_price_state.get("ts_ms", 0) or 0)
        now_ms = int(binance_price_state.get("now_ms", 0) or 0) or ts

        if okx_price <= 0 or b_price <= 0:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason=f"invalid prices okx={okx_price} binance={b_price}",
            )

        # Stale guard
        if b_ts <= 0 or (now_ms - b_ts) > self.stale_ms:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason=f"binance stale age={(now_ms - b_ts)}ms",
            )

        gap_bps = (b_price - okx_price) / okx_price * 10_000

        # Sanity cap
        if abs(gap_bps) > self.max_gap_bps:
            return Signal(
                timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
                reason=f"gap anomaly {gap_bps:+.1f}bps > max {self.max_gap_bps}bps",
            )

        # Entry: Binance > OKX — OKX underpriced, expect catch-up
        if gap_bps >= self.entry_gap_bps:
            confidence = min(0.85, 0.60 + (gap_bps - self.entry_gap_bps) / 10.0)
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=confidence,
                target_size_usd=self.target_size_usd,
                reason=f"binance leads +{gap_bps:.1f}bps",
            )

        # Exit: OKX has caught up or exceeded Binance
        if gap_bps <= self.exit_gap_bps:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.65,
                reason=f"gap closed {gap_bps:+.1f}bps <= exit {self.exit_gap_bps}bps",
            )

        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"gap {gap_bps:+.1f}bps in deadzone",
        )
