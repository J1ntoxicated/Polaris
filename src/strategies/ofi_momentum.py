"""Order Flow Imbalance Momentum — pure (P6).

HYPOTHESIS-016: OFI = recent trades signed volume cumulation.
- buy aggressor: +size, sell aggressor: -size
- recent N trades cumulative OFI > threshold → directional pressure
- 가격 변동 동방향 confirm (last > vwap * 1.0005) → entry

Logic (`evaluate_ofi(tick, trades_recent)`):
- trades_recent = list[(ts, side, size, price)] — 최근 50 trades from OKX trades WS
- ofi = sum(size if side=='buy' else -size for t in trades_recent)
- normalized: ofi / sum(abs(size)) ∈ [-1, 1]
- vwap = sum(price * size) / sum(size)
- entry_long: ofi_norm > 0.40 AND last > vwap * 1.0005 (가격 동방향 confirm)
- exit: ofi_norm < -0.20 (sell pressure 시작)

차별점 (vs HYPO-012 TradeFlow):
- TradeFlow: buy_count / total_count (count 기준, naive)
- OFI: signed volume cumulation + vwap confirmation (price + volume 결합)
- 가격 confirmation으로 lagging 회피
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

# Thresholds — Chordia et al. 2021 기반, hysteresis 설계
# entry: 0.40 (강한 buy pressure), exit: -0.20 (sell 시작 지점)
# deadzone: -0.20 ~ 0.40 — noise / neutral 구간 → flip-flop 방지
DEFAULT_OFI_ENTRY: float = 0.40
DEFAULT_OFI_EXIT: float = -0.20
DEFAULT_MIN_TRADES: int = 10
DEFAULT_PRICE_CONFIRM_BPS: float = 0.0005  # vwap 대비 0.05% 상승 확인
DEFAULT_TARGET_SIZE_USD: float = 100.0


def evaluate_ofi(trades: list[tuple]) -> tuple[float, float]:
    """Compute OFI normalized and VWAP from recent trades.

    Args:
        trades: list of (ts_ms, side, size, price) — OKX trades WS format.

    Returns:
        (ofi_norm, vwap): ofi_norm ∈ [-1.0, 1.0], vwap = 0.0 if empty.

    Pure function — no I/O, deterministic.
    """
    if not trades:
        return 0.0, 0.0

    ofi = 0.0
    total_size = 0.0
    price_size_sum = 0.0

    for _, side, size, price in trades:
        size_f = float(size)
        price_f = float(price)
        total_size += size_f
        price_size_sum += price_f * size_f
        if side == "buy":
            ofi += size_f
        else:
            ofi -= size_f

    if total_size == 0.0:
        return 0.0, 0.0

    ofi_norm = ofi / total_size
    vwap = price_size_sum / total_size
    return ofi_norm, vwap


class OFIMomentum(Strategy):
    """Order Flow Imbalance Momentum — pure (P6).

    HYPOTHESIS-016: OFI signed volume + price VWAP confirmation.
    Addresses HYPO-012 TradeFlow failure (naive count ratio, lagging).

    # Phase 2g Round 13 deprecate trigger (Codex 88% 합의):
    # n=10 도달 시 TP hit count == 0 → 즉시 REALTIME_HYPOS 제거 (HYPO-012 동일 패턴 재발)
    # 현재 (n=5): TP 0 / SL 0 / signal_exit 5 → trigger 모니터링 중
    """

    name = "ofi_momentum"
    min_window = 1

    def __init__(
        self,
        ofi_entry: float = DEFAULT_OFI_ENTRY,
        ofi_exit: float = DEFAULT_OFI_EXIT,
        min_trades: int = DEFAULT_MIN_TRADES,
        price_confirm_bps: float = DEFAULT_PRICE_CONFIRM_BPS,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ) -> None:
        self.ofi_entry = ofi_entry
        self.ofi_exit = ofi_exit
        self.min_trades = min_trades
        self.price_confirm_bps = price_confirm_bps
        self.target_size_usd = target_size_usd

    def evaluate(self, window: list[Candle]) -> Signal:
        """Strategy ABC stub — OFIMomentum uses evaluate_ofi, not candles."""
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="needs trades — use evaluate_ofi",
        )

    def evaluate_ofi(self, tick: dict, trades_recent: list[tuple]) -> Signal:
        """Evaluate OFI signal from tick + recent trades.

        Args:
            tick: OKX ticker dict with 'ts', 'last' fields.
            trades_recent: list[(ts_ms, side, size, price)] — recent N trades.

        Returns:
            Signal — ENTER_LONG / EXIT / HOLD.

        Pure function — no I/O, deterministic.
        """
        ts = int(tick.get("ts", 0) or 0) or 1
        last = float(tick.get("last", 0) or 0)

        # Gate 1: minimum data
        if len(trades_recent) < self.min_trades:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"insufficient trades {len(trades_recent)} < {self.min_trades}",
            )

        ofi_norm, vwap = evaluate_ofi(trades_recent)

        # EXIT: sell pressure regardless of price direction
        if ofi_norm <= self.ofi_exit:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.65,
                reason=f"ofi_norm={ofi_norm:.3f} <= {self.ofi_exit} (sell pressure)",
            )

        # ENTRY: buy pressure + price confirmation (lagging 회피 핵심)
        if ofi_norm >= self.ofi_entry:
            price_confirm_threshold = vwap * (1.0 + self.price_confirm_bps)
            if vwap > 0 and last > price_confirm_threshold:
                return Signal(
                    timestamp_ms=ts,
                    action=SignalAction.ENTER_LONG,
                    confidence=0.75,
                    target_size_usd=self.target_size_usd,
                    reason=(
                        f"ofi_norm={ofi_norm:.3f} >= {self.ofi_entry}"
                        f" + last={last:.4f} > vwap*{1+self.price_confirm_bps:.4f}={price_confirm_threshold:.4f}"
                    ),
                )
            else:
                return Signal(
                    timestamp_ms=ts,
                    action=SignalAction.HOLD,
                    confidence=0.0,
                    reason=(
                        f"ofi_norm={ofi_norm:.3f} >= {self.ofi_entry}"
                        f" but price not confirming: last={last:.4f} <= vwap_confirm={price_confirm_threshold:.4f}"
                    ),
                )

        # HOLD: deadzone
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=f"ofi_norm={ofi_norm:.3f} in deadzone",
        )
