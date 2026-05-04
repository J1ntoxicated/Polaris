"""Liquidation Cascade Mean Reversion — pure (P6).

HYPOTHESIS-023: Binance perp 대규모 청산 직후 mean-revert 포착.

Logic (`evaluate_cascade(tick, liq_pressure, price_60s_ago)`):
  Short 청산 dominant (강제 매수 → 가격 spike → over-extension) + panic drop 신호:
  1. liq_pressure.total_usd >= min_total_usd ($30k) — 충분한 cascade 규모
     (INSIGHT-035: $1M → $100k → $30k; rare event, 300s window 필요)
  2. liq_pressure.imbalance < imbalance_threshold (-0.6) — short 청산 dominant
     (imbalance = (long_liq - short_liq) / total; 음수 = short 청산 우세)
  3. price drop >= price_drop_threshold (0.4%) — panic 신호 (short 청산 후 over-extension)
     OR price_60s_ago=None → drop guard 스킵
  → ENTER_LONG (mean-revert 기대)

  SPOT-only 제약:
  - long 청산 dominant (imbalance > threshold) → downtrend 신호 → SPOT에서 진입 불가 → HOLD
  - short 청산 dominant → 가격 spike 직후 over-extension → mean-revert 기회

Exit:
  - total_usd < exit_total_threshold ($10k) — pressure 완화 → EXIT
  - 그 외 → HOLD (position은 runner의 TP/SL/max_hold가 처리)

Edge 추정:
  - Fee round-trip: 0.28% (OKX 0.14% × 2)
  - Expected revert: 0.5-2% → 양수 EV
  - Backtest 불가 (historical forceOrder 미제공) → paper forward only

HYPO-023 SPOT-only 설계 (ADR-001):
  ENTER_SHORT 미사용. long 청산 cascade = downtrend 진입 위험 → skip.
"""
from __future__ import annotations

from typing import Optional

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

# Default thresholds
# INSIGHT-035 fix #1: $1M → $100k (25h / 612 events = 0 signals — too high for 60s window).
# INSIGHT-035 fix #2: $100k → $30k + window 60s → 300s (300s window diagnosis: still 0 signals.
#   Binance perp forceOrder rare event even for BTC/ETH on low-vol days.
#   $30k threshold + 300s window captures small-to-medium cascades that were being missed.)
DEFAULT_MIN_TOTAL_USD: float = 30_000.0         # $30k cascade threshold (was $100k, rare event fix)
DEFAULT_IMBALANCE_THRESHOLD: float = -0.6       # short 청산 dominant (음수)
DEFAULT_PRICE_DROP_THRESHOLD: float = 0.004     # 0.4% drop (panic signal)
DEFAULT_EXIT_TOTAL_THRESHOLD: float = 10_000.0  # $10k — pressure 완화 EXIT (scaled with $30k entry)
DEFAULT_TARGET_SIZE_USD: float = 200.0


class LiquidationCascade(Strategy):
    """Liquidation Cascade Mean Reversion — pure (P6).

    HYPOTHESIS-023: Binance perp 대규모 short 청산 직후 mean-revert.
    SPOT-only entry (ADR-001). Binance PERP는 data source only.
    """

    name = "liquidation_cascade"
    min_window = 1

    def __init__(
        self,
        min_total_usd: float = DEFAULT_MIN_TOTAL_USD,
        imbalance_threshold: float = DEFAULT_IMBALANCE_THRESHOLD,
        price_drop_threshold: float = DEFAULT_PRICE_DROP_THRESHOLD,
        exit_total_threshold: float = DEFAULT_EXIT_TOTAL_THRESHOLD,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.min_total_usd = min_total_usd
        self.imbalance_threshold = imbalance_threshold
        self.price_drop_threshold = price_drop_threshold
        self.exit_total_threshold = exit_total_threshold
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        """Strategy ABC stub — LiquidationCascade uses evaluate_cascade, not candles."""
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="LiquidationCascade requires liq_pressure + tick — use evaluate_cascade",
        )

    def evaluate_cascade(
        self,
        tick: dict,
        liq_pressure: Optional[dict],
        price_60s_ago: Optional[float] = None,
    ) -> Signal:
        """Evaluate liquidation cascade mean-revert signal.

        Args:
            tick: OKX SPOT ticker payload with 'last' and 'ts' fields.
            liq_pressure: output of compute_liquidation_pressure() or None.
                Keys: total_usd, long_liq_usd, short_liq_usd, imbalance, count.
            price_60s_ago: price 60s ago for panic-drop detection (None → skip drop guard).

        Returns:
            Signal — ENTER_LONG / EXIT / HOLD.

        Pure function — no I/O, deterministic.
        """
        ts = int(tick.get("ts", 0) or 0) or 1
        last = float(tick.get("last", 0) or 0)

        # No pressure data → HOLD (can't evaluate)
        if liq_pressure is None:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason="no liquidation pressure data",
            )

        total_usd = float(liq_pressure.get("total_usd", 0) or 0)
        imbalance = float(liq_pressure.get("imbalance", 0) or 0)

        # Exit: pressure recovered (total < exit threshold)
        # Check before entry so "pressure gone" clears position first
        if total_usd < self.exit_total_threshold:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.60,
                reason=(
                    f"pressure_recovered: total_usd=${total_usd:,.0f} "
                    f"< exit_threshold=${self.exit_total_threshold:,.0f}"
                ),
            )

        # Guard 1: total below entry threshold → not a significant cascade
        if total_usd < self.min_total_usd:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=(
                    f"total_usd=${total_usd:,.0f} "
                    f"< min_total_usd=${self.min_total_usd:,.0f}"
                ),
            )

        # Guard 2: long 청산 dominant (imbalance > threshold) → downtrend → SPOT skip
        if imbalance > self.imbalance_threshold:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=(
                    f"long_liq_dominant: imbalance={imbalance:+.3f} "
                    f"> threshold={self.imbalance_threshold:+.3f} "
                    f"(SPOT-only — bearish cascade skip)"
                ),
            )

        # Guard 3: price drop check (panic signal) — only if 60s price available
        if price_60s_ago is not None and price_60s_ago > 0 and last > 0:
            drop = (last - price_60s_ago) / price_60s_ago  # negative = drop
            if drop > -self.price_drop_threshold:
                return Signal(
                    timestamp_ms=ts,
                    action=SignalAction.HOLD,
                    confidence=0.0,
                    reason=(
                        f"no_panic: drop={drop*100:+.3f}% "
                        f"> -{self.price_drop_threshold*100:.1f}% threshold"
                    ),
                )
            # Panic confirmed — compute confidence from imbalance magnitude
            confidence = min(0.85, 0.60 + abs(imbalance) * 0.30)
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=confidence,
                target_size_usd=self.target_size_usd,
                reason=(
                    f"liq_cascade: total=${total_usd:,.0f} "
                    f"imbalance={imbalance:+.3f} "
                    f"drop={drop*100:+.3f}%"
                ),
            )

        # No price_60s_ago → drop guard skipped, pressure conditions sufficient
        confidence = min(0.75, 0.55 + abs(imbalance) * 0.25)
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.ENTER_LONG,
            confidence=confidence,
            target_size_usd=self.target_size_usd,
            reason=(
                f"liq_cascade (no_price_ref): total=${total_usd:,.0f} "
                f"imbalance={imbalance:+.3f}"
            ),
        )
