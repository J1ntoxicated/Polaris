"""Signal domain model — pure (P6).

Strategy decision output. Immutable.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SignalAction(Enum):
    HOLD = "hold"
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"  # Polaris ADR-001: spot only, but enum reserved for ADR-009 PERP path
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class Signal:
    """Strategy output. Immutable.

    Invariants:
    - confidence in [0.0, 1.0]
    - timestamp_ms > 0
    - if action != HOLD: target_size_usd > 0
    """

    timestamp_ms: int
    action: SignalAction
    confidence: float
    target_size_usd: float = 0.0
    reason: str = ""

    def __post_init__(self) -> None:
        if self.timestamp_ms <= 0:
            raise ValueError(f"timestamp_ms must be positive, got {self.timestamp_ms}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")
        # Entry actions (LONG/SHORT) require positive size.
        # EXIT/HOLD allow zero (exit는 기존 position 종료, size는 의미 없음).
        if self.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT) and self.target_size_usd <= 0:
            raise ValueError(
                f"target_size_usd must be > 0 for entry action, got {self.target_size_usd}"
            )

    @property
    def is_entry(self) -> bool:
        return self.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT)

    @property
    def is_exit(self) -> bool:
        return self.action == SignalAction.EXIT

    @property
    def is_hold(self) -> bool:
        return self.action == SignalAction.HOLD
