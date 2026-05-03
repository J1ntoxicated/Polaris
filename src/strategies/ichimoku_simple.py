"""Ichimoku Cloud (simplified) — pure (P6).

HYPOTHESIS-006: Tenkan/Kijun crossover for 1d trend (Ichimoku 단순화 — 5 components 중 핵심 2개).
- Tenkan (9 mid) crosses above Kijun (26 mid) → ENTER_LONG
- Tenkan crosses below Kijun → EXIT
- Long-only (Polaris ADR-001 SPOT).

Full Ichimoku에는 senkou span A/B (cloud) + chikou span (lag) 추가.
이 단순화는 SMA crossover 변형 (mid price 사용 vs close).
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

DEFAULT_TENKAN = 9
DEFAULT_KIJUN = 26
DEFAULT_TARGET_SIZE_USD = 1000.0


def compute_mid(candles: list[Candle], period: int) -> float:
    """Ichimoku mid = (highest_high + lowest_low) / 2 over period."""
    if len(candles) < period:
        return 0.0
    window = candles[-period:]
    high = max(c.high for c in window)
    low = min(c.low for c in window)
    return (high + low) / 2.0


class IchimokuSimple(Strategy):
    name = "ichimoku_simple"

    def __init__(
        self,
        tenkan: int = DEFAULT_TENKAN,
        kijun: int = DEFAULT_KIJUN,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
    ):
        if tenkan >= kijun:
            raise ValueError(f"tenkan {tenkan} must be < kijun {kijun}")
        self.tenkan = tenkan
        self.kijun = kijun
        self.target_size_usd = target_size_usd
        self.min_window = kijun + 1

    def evaluate(self, window: list[Candle]) -> Signal:
        self._ensure_window(window)
        ts = window[-1].timestamp_ms
        tenkan_now = compute_mid(window, self.tenkan)
        kijun_now = compute_mid(window, self.kijun)
        tenkan_prev = compute_mid(window[:-1], self.tenkan)
        kijun_prev = compute_mid(window[:-1], self.kijun)

        if tenkan_prev <= kijun_prev and tenkan_now > kijun_now:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.7,
                target_size_usd=self.target_size_usd,
                reason=f"Tenkan {tenkan_now:.2f} crosses above Kijun {kijun_now:.2f}",
            )
        if tenkan_prev >= kijun_prev and tenkan_now < kijun_now:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.7,
                reason=f"Tenkan {tenkan_now:.2f} crosses below Kijun {kijun_now:.2f}",
            )
        return Signal(
            timestamp_ms=ts, action=SignalAction.HOLD, confidence=0.0,
            reason=f"Tenkan {tenkan_now:.2f} vs Kijun {kijun_now:.2f}",
        )
