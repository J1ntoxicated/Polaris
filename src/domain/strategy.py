"""Strategy ABC — pure (P6).

Strategy는 candle window를 받아 Signal 반환. No I/O, deterministic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.candle import Candle
from src.domain.signal import Signal


class Strategy(ABC):
    """Strategy base class — pure function wrapper.

    Subclass must implement `evaluate(window)` as deterministic pure function.
    """

    name: str = "base"
    min_window: int = 1  # 최소 candle 수 (e.g. RSI(14) → 15)

    @abstractmethod
    def evaluate(self, window: list[Candle]) -> Signal:
        """주어진 candle window 끝에서 signal 결정.

        Args:
            window: 시간순 candle list (가장 최근이 마지막).

        Returns:
            Signal — HOLD/ENTER_LONG/ENTER_SHORT/EXIT.

        Raises:
            ValueError: window 가 min_window 미만.
        """
        ...

    def _ensure_window(self, window: list[Candle]) -> None:
        if len(window) < self.min_window:
            raise ValueError(
                f"{self.name} requires window >= {self.min_window}, got {len(window)}"
            )
