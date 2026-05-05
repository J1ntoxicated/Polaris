"""Tests for src/paper/dispatchers.py — registry pattern."""
from __future__ import annotations

import pytest

from src.domain.signal import Signal, SignalAction
from src.paper.dispatchers import (
    DispatchContext,
    DISPATCHERS,
    get_dispatcher,
    is_registered,
    register_dispatcher,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Save + restore registry across each test (avoid cross-test pollution)."""
    saved = dict(DISPATCHERS)
    DISPATCHERS.clear()
    yield
    DISPATCHERS.clear()
    DISPATCHERS.update(saved)


class TestRegistry:
    def test_register_and_lookup(self):
        @register_dispatcher("test_tf")
        def _disp(ctx):
            return None
        assert is_registered("test_tf")
        assert get_dispatcher("test_tf") is _disp

    def test_double_register_raises(self):
        @register_dispatcher("dup")
        def _a(ctx):
            return None
        with pytest.raises(ValueError, match="already registered"):
            @register_dispatcher("dup")
            def _b(ctx):
                return None

    def test_lookup_unknown_returns_none(self):
        assert get_dispatcher("nonexistent") is None
        assert not is_registered("nonexistent")

    def test_reset_clears_registry(self):
        @register_dispatcher("x")
        def _disp(ctx):
            return None
        reset_registry()
        assert not is_registered("x")


class TestDispatchContext:
    def test_context_is_frozen(self):
        # frozen dataclass — fields immutable
        class FakeStrategy:
            name = "fake"
        ctx = DispatchContext(
            strategy=FakeStrategy(),  # type: ignore[arg-type]
            hypo={"hypo_id": "X"},
            ticker="BTC-USDT",
            tick_ts_ms=1,
            tick_price=100.0,
            full_tick=None,
            book={},
            bid=99.99,
            ask=100.01,
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            ctx.tick_price = 999.0  # type: ignore[misc]


class TestDispatcherIntegration:
    """Verify a registered dispatcher receives ctx and returns Signal."""

    def test_dispatch_returns_signal(self):
        @register_dispatcher("integration")
        def _disp(ctx: DispatchContext):
            return Signal(
                timestamp_ms=ctx.tick_ts_ms,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"got_ticker={ctx.ticker}",
            )

        class FakeStrategy:
            name = "test"
        ctx = DispatchContext(
            strategy=FakeStrategy(),  # type: ignore[arg-type]
            hypo={"hypo_id": "X"},
            ticker="ETH-USDT",
            tick_ts_ms=42,
            tick_price=2000.0,
            full_tick={"last": 2000.0},
            book={"bids": [], "asks": []},
            bid=1999.99,
            ask=2000.01,
        )
        signal = get_dispatcher("integration")(ctx)
        assert signal is not None
        assert signal.action == SignalAction.HOLD
        assert "ETH-USDT" in signal.reason
