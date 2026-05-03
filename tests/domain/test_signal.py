"""tests/domain/test_signal.py — Pure P6 + P7."""
from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from src.domain.signal import Signal, SignalAction


class TestSignalConstruction:
    def test_hold_default(self) -> None:
        s = Signal(timestamp_ms=1, action=SignalAction.HOLD, confidence=0.5)
        assert s.is_hold
        assert not s.is_entry
        assert not s.is_exit
        assert s.target_size_usd == 0.0

    def test_enter_long_requires_size(self) -> None:
        with pytest.raises(ValueError, match="target_size_usd"):
            Signal(timestamp_ms=1, action=SignalAction.ENTER_LONG, confidence=0.8)

    def test_enter_long_valid(self) -> None:
        s = Signal(
            timestamp_ms=1, action=SignalAction.ENTER_LONG,
            confidence=0.8, target_size_usd=1000.0, reason="rsi<30"
        )
        assert s.is_entry
        assert s.target_size_usd == 1000.0

    def test_exit_no_size_required(self) -> None:
        # exit는 기존 position 종료라 size=0 OK
        s = Signal(timestamp_ms=1, action=SignalAction.EXIT, confidence=1.0)
        assert s.is_exit

    def test_negative_timestamp(self) -> None:
        with pytest.raises(ValueError, match="timestamp_ms"):
            Signal(timestamp_ms=-1, action=SignalAction.HOLD, confidence=0.5)

    def test_confidence_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            Signal(timestamp_ms=1, action=SignalAction.HOLD, confidence=1.5)
        with pytest.raises(ValueError, match="confidence"):
            Signal(timestamp_ms=1, action=SignalAction.HOLD, confidence=-0.1)


class TestSignalImmutable:
    def test_frozen(self) -> None:
        s = Signal(timestamp_ms=1, action=SignalAction.HOLD, confidence=0.5)
        with pytest.raises((AttributeError, TypeError)):
            s.confidence = 0.9  # type: ignore[misc]


# Property-based (P7)


@st.composite
def valid_hold_signal(draw):
    return Signal(
        timestamp_ms=draw(st.integers(min_value=1, max_value=2**62)),
        action=SignalAction.HOLD,
        confidence=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
    )


@st.composite
def valid_entry_signal(draw):
    return Signal(
        timestamp_ms=draw(st.integers(min_value=1, max_value=2**62)),
        action=draw(st.sampled_from([SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT])),
        confidence=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
        target_size_usd=draw(st.floats(min_value=0.01, max_value=1e9, allow_nan=False)),
    )


class TestSignalInvariants:
    @given(s=valid_hold_signal())
    def test_hold_size_zero(self, s) -> None:
        assert s.is_hold
        assert s.target_size_usd == 0.0

    @given(s=valid_entry_signal())
    def test_entry_size_positive(self, s) -> None:
        assert s.is_entry
        assert s.target_size_usd > 0
