"""Market regime detector — BTC 1D SMA + crisis trigger.

Pure function (P6). Caller provides candles (shell fetches them).

Regimes (North Star mandate):
    "crisis"    — 24h drop >= 8% → max bet (fear = opportunity)
    "uptrend"   — sma20 > sma50 * 1.02 AND last > sma20
    "downtrend" — sma20 < sma50 * 0.98 AND last < sma20
    "flat"      — default (anything in between)

References:
- INSIGHT-032: Phase 2j AI sizing
- north_star.md: crisis escalation = max bet
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

_MIN_CANDLES = 50
_CRISIS_THRESHOLD = 0.08    # 24h drop >= 8% → crisis
_UPTREND_GAP = 1.02         # sma20 must be >= sma50 * 1.02
_DOWNTREND_GAP = 0.98       # sma20 must be <= sma50 * 0.98


@runtime_checkable
class _HasClose(Protocol):
    """Duck-type protocol — any object with a .close attribute works."""

    close: float


def detect_regime(btc_candles_1d: list) -> str:
    """Detect current market regime from BTC daily candles.

    Args:
        btc_candles_1d: List of candle-like objects with .close attribute.
                        Ordered oldest-first.

    Returns:
        One of: "crisis", "uptrend", "downtrend", "flat".
        Returns "flat" if insufficient data (< 50 candles).
    """
    if len(btc_candles_1d) < _MIN_CANDLES:
        return "flat"

    closes = [c.close for c in btc_candles_1d[-_MIN_CANDLES:]]
    last = closes[-1]
    prev = closes[-2]   # candle before last (24h ago in 1D data)

    # 1. Crisis check — takes priority (North Star: fear = max bet)
    if prev > 0:
        change_24h = (last - prev) / prev
        if change_24h <= -_CRISIS_THRESHOLD:
            return "crisis"

    # 2. SMA-based trend detection
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes) / 50

    if sma20 >= sma50 * _UPTREND_GAP and last >= sma20:
        return "uptrend"
    if sma20 <= sma50 * _DOWNTREND_GAP and last <= sma20:
        return "downtrend"

    return "flat"
