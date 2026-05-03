"""Candle domain model — pure (P6).

OHLCV bar with timestamp. Immutable, deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candle:
    """OHLCV candle. Immutable.

    Invariants (P7 property-based test):
    - low <= open, close, high
    - high >= open, close, low
    - volume >= 0
    - timestamp_ms > 0
    """

    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.timestamp_ms <= 0:
            raise ValueError(f"timestamp_ms must be positive, got {self.timestamp_ms}")
        if self.volume < 0:
            raise ValueError(f"volume must be non-negative, got {self.volume}")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError(f"low ({self.low}) must be <= min(open, close, high)")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError(f"high ({self.high}) must be >= max(open, close, low)")

    @property
    def hl_range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open
