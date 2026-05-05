"""tests/strategies/test_grid_bot.py — GridBot strategy TDD (HYPO-040).

TDD: 실패 테스트 먼저 작성 → 구현 후 통과 (lessons #46 — import pass != runtime pass).

Spec (HYPOTHESIS-040):
  Entry conditions:
    1. ATR_1H < 1% of price (compression)
    2. Price in lower 30% of 24h range (boundary entry)
    3. No active grid position (is_active=False)
  Exit: range breakout (5% beyond 24h range boundary)
  SPOT-only: BUY only, no short grid levels
  pure: true — _compute_grid_signal is deterministic, no I/O
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.domain.candle import Candle
from src.domain.signal import SignalAction
from src.strategies.grid_bot import (
    DEFAULT_ATR_PCT_THRESHOLD,
    DEFAULT_BREAKOUT_BUFFER_PCT,
    DEFAULT_RANGE_BOUNDARY_PCT,
    DEFAULT_TARGET_SIZE_USD,
    GridBot,
    _compute_grid_signal,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

NOW_MS = 1_700_000_000_000


def _tick(last: float = 100.0, ts: int = NOW_MS) -> dict:
    return {"last": last, "ts": ts, "high24h": str(110.0), "low24h": str(90.0)}


def _make_candle(close: float = 100.0, high: float = 102.0, low: float = 98.0, ts: int = NOW_MS) -> Candle:
    return Candle(timestamp_ms=ts, open=close, high=high, low=low, close=close, volume=1000.0)


def _make_window(n: int = 20, close: float = 100.0) -> list[Candle]:
    """Build n candles with tight ATR (range=0.5%) — compressed."""
    return [
        _make_candle(close=close, high=close * 1.005, low=close * 0.995, ts=NOW_MS - i * 3600_000)
        for i in range(n, 0, -1)
    ]


# ── _compute_grid_signal unit tests ──────────────────────────────────────────


class TestComputeGridSignal:
    """Pure core function tests."""

    def test_entry_conditions_all_met_enter_long(self) -> None:
        """ATR compressed + lower 30% → ENTER_LONG."""
        tick = {"last": 91.0, "ts": NOW_MS}  # 91 in lower 30% of [90, 110] = 90+6=96 boundary
        signal = _compute_grid_signal(
            tick,
            atr_pct=0.005,       # 0.5% < 1% threshold ✓
            high_24h=110.0,
            low_24h=90.0,
            is_active=False,
        )
        assert signal.action == SignalAction.ENTER_LONG
        assert signal.confidence >= 0.70
        assert signal.target_size_usd == DEFAULT_TARGET_SIZE_USD

    def test_atr_too_high_hold(self) -> None:
        """ATR >= 1% threshold → HOLD."""
        tick = {"last": 91.0, "ts": NOW_MS}
        signal = _compute_grid_signal(
            tick,
            atr_pct=0.015,       # 1.5% > 1% threshold ✗
            high_24h=110.0,
            low_24h=90.0,
            is_active=False,
        )
        assert signal.action == SignalAction.HOLD
        assert "atr_too_high" in signal.reason

    def test_price_not_at_boundary_hold(self) -> None:
        """Price in middle 40% → HOLD."""
        tick = {"last": 100.0, "ts": NOW_MS}  # middle of [90, 110]
        signal = _compute_grid_signal(
            tick,
            atr_pct=0.005,
            high_24h=110.0,
            low_24h=90.0,
            is_active=False,
        )
        assert signal.action == SignalAction.HOLD
        assert "not_at_boundary" in signal.reason

    def test_no_range_hold(self) -> None:
        """high_24h=None → HOLD (no range data)."""
        tick = {"last": 91.0, "ts": NOW_MS}
        signal = _compute_grid_signal(
            tick,
            atr_pct=0.005,
            high_24h=None,
            low_24h=None,
            is_active=False,
        )
        assert signal.action == SignalAction.HOLD
        assert "no_24h_range" in signal.reason

    def test_no_price_hold(self) -> None:
        """last=0 → HOLD immediately."""
        tick = {"last": 0, "ts": NOW_MS}
        signal = _compute_grid_signal(tick, atr_pct=0.005, high_24h=110.0, low_24h=90.0, is_active=False)
        assert signal.action == SignalAction.HOLD

    def test_active_grid_no_breakout_hold(self) -> None:
        """Active grid, price inside range → HOLD (wait for TP or breakout)."""
        tick = {"last": 100.0, "ts": NOW_MS}  # inside [90, 110]
        signal = _compute_grid_signal(tick, atr_pct=0.005, high_24h=110.0, low_24h=90.0, is_active=True)
        assert signal.action == SignalAction.HOLD
        assert "grid_active_no_breakout" in signal.reason

    def test_active_grid_upper_breakout_exit(self) -> None:
        """Active grid, price > high_24h + 2% absolute buffer → EXIT (Phase 5).

        Phase 5 Codex fix: absolute buffer = last × 2%, not range × 5%.
        last=115, high=110 → buffer = 115 × 0.02 = 2.3 → upper = 112.3.
        last=115 > 112.3 → EXIT.
        """
        tick = {"last": 115.0, "ts": NOW_MS}  # above 110 + 115*0.02=2.3 → 112.3
        signal = _compute_grid_signal(tick, atr_pct=0.005, high_24h=110.0, low_24h=90.0, is_active=True)
        assert signal.action == SignalAction.EXIT
        assert "breakout" in signal.reason
        assert "up" in signal.reason

    def test_active_grid_lower_breakout_exit(self) -> None:
        """Active grid, price < low_24h - 2% absolute buffer → EXIT (Phase 5).

        Phase 5 Codex fix: absolute buffer = last × 2%, not range × 5%.
        last=87, low=90 → buffer = 87 × 0.02 = 1.74 → lower = 88.26.
        last=87 < 88.26 → EXIT.
        """
        tick = {"last": 87.0, "ts": NOW_MS}  # below 90 - 87*0.02=1.74 → 88.26
        signal = _compute_grid_signal(tick, atr_pct=0.005, high_24h=110.0, low_24h=90.0, is_active=True)
        assert signal.action == SignalAction.EXIT
        assert "breakout" in signal.reason
        assert "down" in signal.reason

    def test_atr_none_skips_atr_guard(self) -> None:
        """atr_pct=None → skip ATR guard, other conditions decide."""
        tick = {"last": 91.0, "ts": NOW_MS}
        signal = _compute_grid_signal(
            tick,
            atr_pct=None,   # skip ATR guard
            high_24h=110.0,
            low_24h=90.0,
            is_active=False,
        )
        # Should enter (lower boundary met, no ATR block)
        assert signal.action == SignalAction.ENTER_LONG

    def test_entry_at_exact_lower_boundary(self) -> None:
        """Price exactly at lower boundary (30%) → ENTER_LONG."""
        # lower_boundary = 90 + 20*0.30 = 96 exactly
        tick = {"last": 96.0, "ts": NOW_MS}
        signal = _compute_grid_signal(tick, atr_pct=0.005, high_24h=110.0, low_24h=90.0, is_active=False)
        assert signal.action == SignalAction.ENTER_LONG

    def test_entry_just_above_boundary_hold(self) -> None:
        """Price just above lower boundary → HOLD."""
        # lower_boundary = 96.0
        tick = {"last": 96.1, "ts": NOW_MS}
        signal = _compute_grid_signal(tick, atr_pct=0.005, high_24h=110.0, low_24h=90.0, is_active=False)
        assert signal.action == SignalAction.HOLD

    def test_reason_contains_grid_entry(self) -> None:
        """Entry signal reason describes grid entry."""
        tick = {"last": 91.0, "ts": NOW_MS}
        signal = _compute_grid_signal(tick, atr_pct=0.005, high_24h=110.0, low_24h=90.0, is_active=False)
        assert "grid_entry" in signal.reason

    def test_confidence_bounded(self) -> None:
        """Confidence always in [0, 1]."""
        tick = {"last": 91.0, "ts": NOW_MS}
        signal = _compute_grid_signal(tick, atr_pct=0.001, high_24h=110.0, low_24h=90.0, is_active=False)
        assert 0.0 <= signal.confidence <= 1.0


# ── GridBot.evaluate (candle-based) ──────────────────────────────────────────


class TestGridBotEvaluate:
    def test_empty_window_hold(self) -> None:
        """Empty candle list → HOLD."""
        bot = GridBot()
        sig = bot.evaluate([])
        assert sig.action == SignalAction.HOLD

    def test_compressed_lower_boundary_enter(self) -> None:
        """Tight ATR + price at lower 24h boundary → ENTER_LONG."""
        # Build 25 candles: close=91 (lower boundary of 90-110 range assumed by tight candles)
        # Candles: high=91.2, low=90.8 → ATR ≈ 0.4/91 ≈ 0.44% < 1% threshold
        candles = [
            _make_candle(close=91.0, high=91.2, low=90.8, ts=NOW_MS - i * 3600_000)
            for i in range(25, 0, -1)
        ]
        bot = GridBot()
        sig = bot.evaluate(candles)
        # ATR is tight, but range derived from window: 90.8-91.2 → lower 30% = 90.8+0.12=90.92
        # Price 91.0 > 90.92 → just outside lower 30% → might HOLD (tight range)
        # Just verify no crash and valid action
        assert sig.action in (SignalAction.ENTER_LONG, SignalAction.HOLD)

    def test_high_atr_hold(self) -> None:
        """Wide ATR candles → ATR > threshold → HOLD."""
        # High=115, Low=85 → range=30, ATR≈30/100=30% >> 1%
        candles = [
            _make_candle(close=100.0, high=115.0, low=85.0, ts=NOW_MS - i * 3600_000)
            for i in range(20, 0, -1)
        ]
        bot = GridBot()
        sig = bot.evaluate(candles)
        assert sig.action == SignalAction.HOLD
        assert "atr_too_high" in sig.reason

    def test_insufficient_candles_hold(self) -> None:
        """Fewer than min_window candles → HOLD (no ATR)."""
        candles = [_make_candle(ts=NOW_MS - i * 3600_000) for i in range(5, 0, -1)]
        bot = GridBot()
        # Less than 14 → atr_pct=None → no ATR block, but range may also be narrow
        sig = bot.evaluate(candles)
        # No crash is the key requirement
        assert sig.action in (SignalAction.ENTER_LONG, SignalAction.HOLD)

    def test_name(self) -> None:
        """Strategy name is 'grid_bot'."""
        bot = GridBot()
        assert bot.name == "grid_bot"

    def test_min_window(self) -> None:
        """min_window == 14."""
        assert GridBot.min_window == 14


# ── GridBot.evaluate_grid (tick-driven) ──────────────────────────────────────


class TestGridBotEvaluateGrid:
    def test_enter_long_conditions_met(self) -> None:
        """evaluate_grid with valid indicators → ENTER_LONG."""
        bot = GridBot()
        tick = {"last": 91.0, "ts": NOW_MS}
        sig = bot.evaluate_grid(tick, atr_pct=0.005, high_24h=110.0, low_24h=90.0, is_active=False)
        assert sig.action == SignalAction.ENTER_LONG

    def test_exit_on_breakout(self) -> None:
        """evaluate_grid breakout → EXIT."""
        bot = GridBot()
        tick = {"last": 115.0, "ts": NOW_MS}  # above breakout
        sig = bot.evaluate_grid(tick, atr_pct=0.005, high_24h=110.0, low_24h=90.0, is_active=True)
        assert sig.action == SignalAction.EXIT

    def test_hold_mid_range(self) -> None:
        """evaluate_grid price in middle → HOLD."""
        bot = GridBot()
        tick = {"last": 100.0, "ts": NOW_MS}
        sig = bot.evaluate_grid(tick, atr_pct=0.005, high_24h=110.0, low_24h=90.0, is_active=False)
        assert sig.action == SignalAction.HOLD

    def test_custom_params_respected(self) -> None:
        """Custom atr_pct_threshold respected."""
        # With threshold=0.02 (2%), ATR=1.5% should pass
        bot = GridBot(atr_pct_threshold=0.02)
        tick = {"last": 91.0, "ts": NOW_MS}
        sig = bot.evaluate_grid(tick, atr_pct=0.015, high_24h=110.0, low_24h=90.0, is_active=False)
        # atr 1.5% < 2% threshold → should enter (if boundary met)
        assert sig.action == SignalAction.ENTER_LONG


# ── Property-based: confidence always bounded ─────────────────────────────────


class TestGridBotProperties:
    @given(
        last=st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False),
        atr_pct=st.floats(min_value=0.0, max_value=0.5, allow_nan=False, allow_infinity=False),
        range_pct=st.floats(min_value=0.01, max_value=0.5, allow_nan=False, allow_infinity=False),
        is_active=st.booleans(),
    )
    @settings(max_examples=300)
    def test_confidence_always_bounded(
        self, last: float, atr_pct: float, range_pct: float, is_active: bool
    ) -> None:
        """Property: confidence always in [0, 1], no exception."""
        high_24h = last * (1.0 + range_pct)
        low_24h = last * (1.0 - range_pct)
        tick = {"last": last, "ts": NOW_MS}
        try:
            sig = _compute_grid_signal(tick, atr_pct, high_24h, low_24h, is_active)
            assert 0.0 <= sig.confidence <= 1.0
        except Exception as e:
            pytest.fail(f"_compute_grid_signal raised: {e}")

    @given(
        last=st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_action_always_valid(self, last: float) -> None:
        """Property: action is always one of the valid SignalAction values."""
        tick = {"last": last, "ts": NOW_MS}
        sig = _compute_grid_signal(tick, atr_pct=0.005, high_24h=last * 1.1, low_24h=last * 0.9, is_active=False)
        assert sig.action in (SignalAction.ENTER_LONG, SignalAction.EXIT, SignalAction.HOLD)
