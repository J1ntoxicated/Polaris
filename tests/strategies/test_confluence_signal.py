"""tests/strategies/test_confluence_signal.py — TDD Phase 2h.

Tests:
- test_all_subs_long_emits_long (require_all=True AND logic)
- test_partial_subs_n_of_m (require_all=False N-of-M logic)
- test_no_long_emits_hold (no sub emits ENTER_LONG)
- test_require_all_partial_holds (require_all but only 1 of 2 subs long)
- test_min_window_is_max_of_subs (derived from sub min_windows)
- test_target_size_usd_propagates (entry signal carries correct size)
- test_exit_from_any_sub_exits (EXIT from any sub → meta EXIT)
- test_confidence_is_average_of_longs (confidence = mean of agreeing subs)
- test_empty_subs_raises (guard)
- test_n_of_m_exactly_min (boundary: exactly min_confluence subs agree)
- test_n_of_m_below_min_holds (boundary: one below min → HOLD)
- property: require_all=True → agree count == len(subs)
"""
from __future__ import annotations

import pytest

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy
from src.strategies.confluence_signal import ConfluenceSignal


# ---------------------------------------------------------------------------
# Stub strategies for controlled testing
# ---------------------------------------------------------------------------

def _candle(ts: int = 1_000_000, price: float = 100.0) -> Candle:
    return Candle(timestamp_ms=ts, open=price, high=price * 1.01,
                  low=price * 0.99, close=price, volume=1000)


def _window(n: int = 5, ts_start: int = 1_000_000) -> list[Candle]:
    return [_candle(ts=ts_start + i * 60_000, price=100.0 + i) for i in range(n)]


class StubLong(Strategy):
    """Always emits ENTER_LONG."""
    name = "stub_long"
    min_window = 1

    def __init__(self, confidence: float = 0.8, size: float = 200.0):
        self.confidence = confidence
        self._size = size

    def evaluate(self, window: list[Candle]) -> Signal:
        return Signal(
            timestamp_ms=window[-1].timestamp_ms,
            action=SignalAction.ENTER_LONG,
            confidence=self.confidence,
            target_size_usd=self._size,
            reason="stub_long",
        )


class StubHold(Strategy):
    """Always emits HOLD."""
    name = "stub_hold"
    min_window = 1

    def evaluate(self, window: list[Candle]) -> Signal:
        return Signal(
            timestamp_ms=window[-1].timestamp_ms,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="stub_hold",
        )


class StubExit(Strategy):
    """Always emits EXIT."""
    name = "stub_exit"
    min_window = 1

    def evaluate(self, window: list[Candle]) -> Signal:
        return Signal(
            timestamp_ms=window[-1].timestamp_ms,
            action=SignalAction.EXIT,
            confidence=0.6,
            reason="stub_exit",
        )


class StubMinWindow3(Strategy):
    """Always HOLD but requires min_window=3."""
    name = "stub_min3"
    min_window = 3

    def evaluate(self, window: list[Candle]) -> Signal:
        return Signal(
            timestamp_ms=window[-1].timestamp_ms,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="stub_min3",
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRequireAll:
    """require_all=True (AND logic) — all subs must emit ENTER_LONG."""

    def test_all_subs_long_emits_long(self) -> None:
        """2-of-2 require_all=True → ENTER_LONG."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(), StubLong()],
            require_all=True,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.ENTER_LONG, f"expected ENTER_LONG, got {sig.action}: {sig.reason}"
        assert sig.target_size_usd == pytest.approx(200.0)

    def test_require_all_partial_holds(self) -> None:
        """1-of-2 require_all=True → HOLD (AND logic)."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(), StubHold()],
            require_all=True,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.HOLD, f"expected HOLD, got {sig.action}: {sig.reason}"

    def test_no_long_emits_hold(self) -> None:
        """No subs emit ENTER_LONG → HOLD."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubHold(), StubHold()],
            require_all=True,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.HOLD

    def test_three_subs_all_long(self) -> None:
        """3-of-3 require_all=True → ENTER_LONG."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(), StubLong(), StubLong()],
            require_all=True,
            target_size_usd=150.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.target_size_usd == pytest.approx(150.0)

    def test_three_subs_two_long_one_hold(self) -> None:
        """2-of-3 require_all=True → HOLD (AND failed)."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(), StubLong(), StubHold()],
            require_all=True,
            target_size_usd=150.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.HOLD


class TestNofM:
    """require_all=False — N-of-M (min_confluence) logic."""

    def test_partial_subs_n_of_m(self) -> None:
        """2-of-3, min_confluence=2 → ENTER_LONG when 2 subs agree."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(), StubLong(), StubHold()],
            require_all=False,
            min_confluence=2,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.ENTER_LONG, f"expected ENTER_LONG, got {sig.action}: {sig.reason}"

    def test_n_of_m_exactly_min(self) -> None:
        """Boundary: exactly min_confluence subs agree → ENTER_LONG."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(), StubHold(), StubHold()],
            require_all=False,
            min_confluence=1,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.ENTER_LONG

    def test_n_of_m_below_min_holds(self) -> None:
        """Boundary: below min_confluence → HOLD."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(), StubHold(), StubHold()],
            require_all=False,
            min_confluence=2,  # need 2 but only 1 agrees
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.HOLD

    def test_n_of_m_all_hold(self) -> None:
        """0-of-3, min_confluence=1 → HOLD."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubHold(), StubHold(), StubHold()],
            require_all=False,
            min_confluence=1,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.HOLD


class TestExitBehavior:
    """EXIT propagation from sub-strategies."""

    def test_exit_from_all_hold_subs_no_exit(self) -> None:
        """All HOLD → no EXIT from confluence."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubHold(), StubHold()],
            require_all=True,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        # With no entry → HOLD (EXIT not relevant without position)
        assert sig.action == SignalAction.HOLD

    def test_exit_sub_with_no_long_subs(self) -> None:
        """EXIT from sub with no LONG subs → EXIT propagated."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubExit(), StubHold()],
            require_all=True,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.EXIT

    def test_exit_overrides_long(self) -> None:
        """EXIT from one sub overrides LONG signal (conservative)."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(), StubExit()],
            require_all=True,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        # EXIT should take precedence over partial agree (require_all fails anyway)
        # But when require_all=False and min=1:
        strategy2 = ConfluenceSignal(
            sub_strategies=[StubLong(), StubLong(), StubExit()],
            require_all=False,
            min_confluence=2,
            target_size_usd=200.0,
        )
        sig2 = strategy2.evaluate(_window())
        # 2 LONG pass min_confluence=2, but EXIT from sub → EXIT wins
        assert sig2.action == SignalAction.EXIT


class TestConfidenceAndSize:
    """Confidence aggregation and size propagation."""

    def test_target_size_usd_propagates(self) -> None:
        """Entry signal carries ConfluenceSignal.target_size_usd (not sub's size)."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(size=999.0), StubLong(size=999.0)],
            require_all=True,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.ENTER_LONG
        # Confluence overrides sub size with its own target_size_usd
        assert sig.target_size_usd == pytest.approx(200.0)

    def test_confidence_is_mean_of_agreeing_subs(self) -> None:
        """confidence = mean confidence of ENTER_LONG subs."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(confidence=0.6), StubLong(confidence=0.8)],
            require_all=True,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.ENTER_LONG
        assert sig.confidence == pytest.approx(0.7, abs=0.001)  # (0.6 + 0.8) / 2


class TestMinWindow:
    """min_window is max of sub min_windows."""

    def test_min_window_is_max_of_subs(self) -> None:
        """min_window = max(sub.min_window for sub in subs)."""
        s1 = StubLong()      # min_window = 1
        s2 = StubMinWindow3()  # min_window = 3
        strategy = ConfluenceSignal(
            sub_strategies=[s1, s2],
            require_all=True,
            target_size_usd=200.0,
        )
        assert strategy.min_window == 3

    def test_min_window_respected_in_evaluate(self) -> None:
        """Window shorter than min_window → raises ValueError."""
        s = StubMinWindow3()
        strategy = ConfluenceSignal(
            sub_strategies=[s],
            require_all=True,
            target_size_usd=200.0,
        )
        # window of 2 < min_window 3 → ValueError from _ensure_window
        with pytest.raises(ValueError, match="requires window"):
            strategy.evaluate(_window(n=2))


class TestGuards:
    """Input validation guards."""

    def test_empty_subs_raises(self) -> None:
        """Empty sub_strategies list → ValueError."""
        with pytest.raises(ValueError, match="sub_strategies"):
            ConfluenceSignal(sub_strategies=[], require_all=True, target_size_usd=200.0)

    def test_min_confluence_zero_raises(self) -> None:
        """min_confluence=0 → ValueError (nonsensical threshold)."""
        with pytest.raises(ValueError, match="min_confluence"):
            ConfluenceSignal(
                sub_strategies=[StubLong()],
                require_all=False,
                min_confluence=0,
                target_size_usd=200.0,
            )

    def test_target_size_positive(self) -> None:
        """target_size_usd <= 0 → ValueError."""
        with pytest.raises(ValueError, match="target_size_usd"):
            ConfluenceSignal(
                sub_strategies=[StubLong()],
                require_all=True,
                target_size_usd=0.0,
            )


class TestReasonString:
    """reason field provides diagnostic info."""

    def test_reason_includes_sub_count(self) -> None:
        """ENTER_LONG reason includes n_long / n_subs info."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(), StubLong()],
            require_all=True,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.ENTER_LONG
        assert "2/2" in sig.reason or "confluence" in sig.reason.lower()

    def test_hold_reason_includes_sub_count(self) -> None:
        """HOLD reason shows how many agreed."""
        strategy = ConfluenceSignal(
            sub_strategies=[StubLong(), StubHold()],
            require_all=True,
            target_size_usd=200.0,
        )
        sig = strategy.evaluate(_window())
        assert sig.action == SignalAction.HOLD
        assert "1/2" in sig.reason or "confluence" in sig.reason.lower()


class TestIntegrationWithRealStrategies:
    """Integration: ConfluenceSignal wrapping real strategies."""

    def test_volume_burst_plus_donchian_window(self) -> None:
        """VolumeBurst + DonchianBreakout — min_window = max(21, 21) = 21."""
        from src.strategies.donchian_breakout import DonchianBreakout
        from src.strategies.volume_burst import VolumeBurst

        vb = VolumeBurst(vol_period=20)   # min_window = 21
        dc = DonchianBreakout(entry_period=20, exit_period=10)  # min_window = 21
        strategy = ConfluenceSignal(
            sub_strategies=[vb, dc],
            require_all=True,
            target_size_usd=200.0,
        )
        assert strategy.min_window == 21

    def test_sma_plus_volume_burst_min_window(self) -> None:
        """SMACrossover(50,200) + VolumeBurst — min_window = 201."""
        from src.strategies.sma_crossover import SMACrossover
        from src.strategies.volume_burst import VolumeBurst

        sma = SMACrossover(fast=50, slow=200)  # min_window = 201
        vb = VolumeBurst(vol_period=20)         # min_window = 21
        strategy = ConfluenceSignal(
            sub_strategies=[sma, vb],
            require_all=True,
            target_size_usd=200.0,
        )
        assert strategy.min_window == 201
