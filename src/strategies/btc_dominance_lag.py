"""BTC Dominance Lead-Lag — Cross-asset return predictability.

HYPOTHESIS-034: BTC 5min momentum leads alt follow (30s-3min lag).

Academic basis:
  Stalder, O., Cosenza, M. (2025).
  "Cross-cryptocurrency return predictability".
  - BTC leads alt returns with measurable lag; predictability decays in ~3min.

  Liu, Y. et al. (2022).
  "Common risk factors in cryptocurrency".
  Journal of Finance, 77(2), 1133-1177.
  - BTC dominance as systematic factor driving altcoin co-movement.

Crypto adaptation:
  - BTC 5min price delta > btc_momentum_threshold_pct → strong signal.
  - Alt must be in same direction 24h (filter trend-inconsistent alts).
  - Alt 5min delta < BTC 5min delta → alt lagging → enter long (follow lag).
  - SPOT-only: only long entries, never short.

Logic (evaluate_lag):
  1. btc_delta = (btc_state["price_now"] - btc_state["price_5min_ago"]) / btc_state["price_5min_ago"]
  2. If btc_delta < btc_momentum_threshold_pct → HOLD (BTC momentum too weak)
  3. If btc_state is None or stale → HOLD (warmup / data gap)
  4. If alt_24h_delta_pct <= 0 → HOLD (alt in downtrend, wrong direction)
  5. If alt_5min_delta_pct >= btc_delta → HOLD (alt already caught up, no lag)
  6. → ENTER_LONG (alt lagging confirmed)

Input formats:
  btc_state: {"price_now": float, "price_5min_ago": float, "ts_ms": int}
  alt_context: {"alt_5min_delta_pct": float, "alt_24h_delta_pct": float}
  tick: OKX-style {"last": "price_str", "ts": "ts_ms_str"}

Pure function — no I/O, no module state, deterministic.
P6 classification: pure=true
"""
from __future__ import annotations

from typing import Optional

from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_BTC_MOMENTUM_THRESHOLD_PCT: float = 0.005  # +0.5% in 5 minutes
DEFAULT_MAX_BTC_AGE_MS: int = 300_000              # 5 minutes stale guard
DEFAULT_TARGET_SIZE_USD: float = 200.0


class BTCDominanceLag(Strategy):
    """BTC Dominance Lead-Lag strategy.

    evaluate_lag(tick, btc_state, alt_context) is the main pure interface.
    evaluate(window) returns HOLD (candle-based evaluate not used).

    Attributes:
        btc_momentum_threshold_pct: minimum BTC 5min return to trigger.
        max_btc_age_ms: maximum age of BTC state before considered stale.
        target_size_usd: base position size.
    """

    name = "btc_dominance_lag"
    min_window = 1  # not candle-based; satisfies Strategy ABC

    def __init__(
        self,
        btc_momentum_threshold_pct: float = DEFAULT_BTC_MOMENTUM_THRESHOLD_PCT,
        max_btc_age_ms: int = DEFAULT_MAX_BTC_AGE_MS,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.btc_momentum_threshold_pct = btc_momentum_threshold_pct
        self.max_btc_age_ms = max_btc_age_ms
        self.target_size_usd = target_size_usd

    def evaluate(self, window):  # type: ignore[override]
        """Candle-based evaluate — not used for BTCDominanceLag (tick+state based).

        Returns HOLD always (shell must call evaluate_lag directly).
        """
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="btc_dominance_lag_use_evaluate_lag",
        )

    def evaluate_lag(
        self,
        tick: dict,
        btc_state: Optional[dict],
        alt_context: dict,
        now_ms: Optional[int] = None,
    ) -> Signal:
        """Evaluate BTC lead-lag signal.

        Args:
            tick: OKX-style alt ticker dict {"last": "price_str", "ts": "ts_ms_str"}.
            btc_state: {"price_now": float, "price_5min_ago": float, "ts_ms": int}
                       or None if BTC data not available.
            alt_context: {"alt_5min_delta_pct": float, "alt_24h_delta_pct": float}.
            now_ms: current timestamp ms (default: from tick ts).

        Returns:
            Signal ENTER_LONG if BTC leads and alt lags, HOLD otherwise.
            Never ENTER_SHORT (SPOT-only).

        Pure function — no I/O.
        """
        ts_ms = int(tick.get("ts", 0) or 0) or 1
        current_now_ms = now_ms if now_ms is not None else ts_ms

        # Guard 1: BTC state availability
        if btc_state is None:
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason="btc_lag_hold no_btc_state",
            )

        # Guard 2: BTC state staleness
        btc_age_ms = current_now_ms - btc_state.get("ts_ms", 0)
        if btc_age_ms > self.max_btc_age_ms:
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"btc_lag_hold stale btc_age={btc_age_ms/1000:.0f}s > {self.max_btc_age_ms/1000:.0f}s",
            )

        # Compute BTC 5min momentum
        price_now = btc_state.get("price_now", 0.0) or 0.0
        price_5min_ago = btc_state.get("price_5min_ago", 0.0) or 0.0
        if price_5min_ago <= 0.0:
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason="btc_lag_hold invalid_btc_price",
            )
        btc_delta = (price_now - price_5min_ago) / price_5min_ago

        # Guard 3: BTC momentum threshold
        if btc_delta < self.btc_momentum_threshold_pct:
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"btc_lag_hold btc_delta={btc_delta:.4f} < threshold={self.btc_momentum_threshold_pct:.4f}",
            )

        # Guard 4: Alt 24h trend must be positive (same direction filter)
        alt_24h = alt_context.get("alt_24h_delta_pct", 0.0) or 0.0
        if alt_24h <= 0.0:
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"btc_lag_hold alt_24h={alt_24h:.4f} negative_trend",
            )

        # Guard 5: Alt must not have already caught up to BTC
        alt_5min = alt_context.get("alt_5min_delta_pct", 0.0) or 0.0
        if alt_5min >= btc_delta:
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"btc_lag_hold alt_caught_up alt_5m={alt_5min:.4f} >= btc_5m={btc_delta:.4f}",
            )

        # All guards passed → alt is lagging BTC momentum → ENTER_LONG
        lag_gap = btc_delta - alt_5min
        confidence = min(0.95, 0.5 + lag_gap * 20.0)  # scale with gap size, cap at 0.95
        return Signal(
            timestamp_ms=ts_ms,
            action=SignalAction.ENTER_LONG,
            confidence=round(confidence, 3),
            target_size_usd=self.target_size_usd,
            reason=(
                f"btc_lag_enter btc_5m={btc_delta:.4f} alt_5m={alt_5min:.4f} "
                f"lag_gap={lag_gap:.4f} alt_24h={alt_24h:.4f}"
            ),
        )
