"""VPIN Toxicity — Volume-Synchronized Probability of Informed Trading.

HYPOTHESIS-033: High VPIN → informed trading flow → momentum entry.

Academic basis:
  Easley, D., Lopez de Prado, M., O'Hara, M. (2012).
  "Flow Toxicity and Liquidity in a High-frequency World".
  Review of Financial Studies, 25(5), 1457-1493.
  - VPIN = sum(|buy_vol - sell_vol|) / sum(total_vol) over N volume buckets.
  - High VPIN → informed traders dominating → price impact imminent.
  - Used by exchanges to detect toxic order flow and adjust spreads.

Crypto adaptation:
  - Volume buckets = fixed USD-notional chunks (e.g. $50k for BTC, $5k for DOGE).
  - Shell (realtime_runner) maintains rolling bucket list from OKX trades WS.
  - VPIN > high_toxicity_threshold (0.7) → informed buying → ENTER_LONG.
  - VPIN < low_toxicity_threshold (0.3) → balanced / uninformed → HOLD.
  - SPOT-only: never ENTER_SHORT.

Logic:
  VPIN = sum(|buy_vol_i - sell_vol_i|) / sum(buy_vol_i + sell_vol_i) for i in N buckets
  if VPIN >= high_toxicity_threshold → ENTER_LONG (momentum)
  else → HOLD

Bucket format: {"buy_vol": float, "sell_vol": float}
Tick format: OKX-style dict {"last": "price_str", "ts": "ts_ms_str"}

Pure function — no I/O, no module state, deterministic.
P6 classification: pure=true
"""
from __future__ import annotations

from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_HIGH_TOXICITY_THRESHOLD: float = 0.7
DEFAULT_LOW_TOXICITY_THRESHOLD: float = 0.3
DEFAULT_MIN_BUCKETS: int = 10
DEFAULT_TARGET_SIZE_USD: float = 200.0


class VPINToxicity(Strategy):
    """VPIN Toxicity strategy — informed trading flow detection.

    evaluate_vpin(tick, buckets) is the main pure interface.
    evaluate(window) delegates to HOLD (candle-based evaluate not used).

    Attributes:
        high_toxicity_threshold: VPIN >= this → ENTER_LONG.
        low_toxicity_threshold: VPIN < this → HOLD.
        min_buckets: minimum bucket count to compute meaningful VPIN.
        target_size_usd: base position size.
    """

    name = "vpin_toxicity"
    min_window = 1  # not candle-based; satisfies Strategy ABC

    def __init__(
        self,
        high_toxicity_threshold: float = DEFAULT_HIGH_TOXICITY_THRESHOLD,
        low_toxicity_threshold: float = DEFAULT_LOW_TOXICITY_THRESHOLD,
        min_buckets: int = DEFAULT_MIN_BUCKETS,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.high_toxicity_threshold = high_toxicity_threshold
        self.low_toxicity_threshold = low_toxicity_threshold
        self.min_buckets = min_buckets
        self.target_size_usd = target_size_usd

    def evaluate(self, window):  # type: ignore[override]
        """Candle-based evaluate — not used for VPIN (tick+bucket based).

        Returns HOLD always (shell must call evaluate_vpin directly).
        """
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="vpin_use_evaluate_vpin",
        )

    def compute_vpin(self, buckets: list[dict]) -> float:
        """Compute VPIN from volume buckets.

        Args:
            buckets: list of {"buy_vol": float, "sell_vol": float}.

        Returns:
            VPIN in [0.0, 1.0]. 0.0 for empty or zero-volume input.

        Pure function — no I/O.
        """
        if not buckets:
            return 0.0

        total_imbalance = 0.0
        total_volume = 0.0
        for b in buckets:
            buy = b.get("buy_vol", 0.0) or 0.0
            sell = b.get("sell_vol", 0.0) or 0.0
            vol = buy + sell
            if vol <= 0.0:
                continue
            total_imbalance += abs(buy - sell)
            total_volume += vol

        if total_volume <= 0.0:
            return 0.0

        return total_imbalance / total_volume

    def evaluate_vpin(self, tick: dict, buckets: list[dict]) -> Signal:
        """Evaluate VPIN signal from tick + volume buckets.

        Args:
            tick: OKX-style dict {"last": "price_str", "ts": "ts_ms_str"}.
            buckets: rolling volume buckets (most recent N).

        Returns:
            Signal with action ENTER_LONG / HOLD.
            Never ENTER_SHORT (SPOT-only constraint).

        Pure function — no I/O, no side effects.
        """
        ts_ms = int(tick.get("ts", 0) or 0) or 1

        if len(buckets) < self.min_buckets:
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"vpin_warmup buckets={len(buckets)} < {self.min_buckets}",
            )

        vpin = self.compute_vpin(buckets)

        if vpin >= self.high_toxicity_threshold:
            return Signal(
                timestamp_ms=ts_ms,
                action=SignalAction.ENTER_LONG,
                confidence=round(vpin, 3),
                target_size_usd=self.target_size_usd,
                reason=f"vpin_toxicity_high vpin={vpin:.3f} >= {self.high_toxicity_threshold}",
            )

        return Signal(
            timestamp_ms=ts_ms,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"vpin_hold vpin={vpin:.3f} < {self.high_toxicity_threshold}",
        )
