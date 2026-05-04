"""Tests for dynamic_sizing — TDD red/green cycle (Phase 2j).

모태 Kelly + confidence + regime + drawdown 조합 검증.
Property-based test: 0 <= fraction <= MAX_FRACTION (P7).
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.risk.dynamic_sizing import (
    MAX_FRACTION,
    MIN_SIZE_USD,
    REGIME_MULT,
    SizingInputs,
    SizingOutput,
    compute_size,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _inputs(
    cash_usd: float = 10_000.0,
    signal_confidence: float = 0.80,
    recent_win_rate: float = 0.55,
    recent_avg_win_pct: float = 0.8,
    recent_avg_loss_pct: float = 0.5,
    regime: str = "flat",
    drawdown_pct: float = 0.0,
) -> SizingInputs:
    return SizingInputs(
        cash_usd=cash_usd,
        signal_confidence=signal_confidence,
        recent_win_rate=recent_win_rate,
        recent_avg_win_pct=recent_avg_win_pct,
        recent_avg_loss_pct=recent_avg_loss_pct,
        regime=regime,
        drawdown_pct=drawdown_pct,
    )


# ── unit tests ─────────────────────────────────────────────────────────────

class TestHighConfidenceUptrend:
    """High confidence + uptrend → larger size than baseline flat."""

    def test_high_confidence_uptrend_full_kelly(self) -> None:
        baseline = compute_size(_inputs(regime="flat"))
        uptrend = compute_size(_inputs(regime="uptrend", signal_confidence=0.9))
        assert uptrend.size_usd > baseline.size_usd

    def test_uptrend_regime_mult_1x(self) -> None:
        result = compute_size(_inputs(regime="uptrend"))
        assert result.fraction > 0
        assert "uptrend" in result.reason


class TestLowConfidenceReduceSize:
    """Low confidence → size significantly below high-confidence case."""

    def test_low_confidence_reduce_size(self) -> None:
        low = compute_size(_inputs(signal_confidence=0.5))
        high = compute_size(_inputs(signal_confidence=0.9))
        # confidence² : 0.25 vs 0.81 → low should be much smaller
        assert low.size_usd < high.size_usd

    def test_confidence_squared_applied(self) -> None:
        # conf=0.5 → conf²=0.25; conf=1.0 → conf²=1.0 (4x difference)
        # Use cold-start kelly (win_rate=0) to avoid MAX_FRACTION cap distorting ratio.
        # cold kelly=0.05 × conf²=0.25 × flat=0.7 × dd=1.0 = 0.00875 (well under cap)
        # Phase 2N: MIN_SIZE_USD=$100 — use large cash to avoid skip masking ratio.
        low = compute_size(_inputs(cash_usd=100_000.0, signal_confidence=0.5, regime="flat", recent_win_rate=0.0))
        perfect = compute_size(_inputs(cash_usd=100_000.0, signal_confidence=1.0, regime="flat", recent_win_rate=0.0))
        ratio = perfect.fraction / low.fraction if low.fraction > 0 else float("inf")
        # Should be ~4x (conf² ratio: 1.0 / 0.25 = 4.0)
        assert ratio == pytest.approx(4.0, rel=0.05)


class TestDowntrendSize:
    """Downtrend → regime_mult=0.3 → size reduced to ~30% of uptrend."""

    def test_downtrend_size_30pct(self) -> None:
        down = compute_size(_inputs(regime="downtrend", drawdown_pct=0.0))
        up = compute_size(_inputs(regime="uptrend", drawdown_pct=0.0))
        # downtrend mult 0.3 vs uptrend mult 1.0 → ratio ~0.3
        if up.fraction > 0:
            ratio = down.fraction / up.fraction
            assert ratio == pytest.approx(0.30, rel=0.01)

    def test_downtrend_reason_string(self) -> None:
        result = compute_size(_inputs(regime="downtrend"))
        assert "downtrend" in result.reason


class TestCrisisSize:
    """Crisis → regime_mult=1.5 → 1.5× uptrend size (북극성 mandate)."""

    def test_crisis_size_150pct(self) -> None:
        # Use cold-start kelly to avoid MAX_FRACTION cap distorting the ratio.
        # cold kelly=0.05 × conf²=0.64 × mult × dd=1.0 = 0.032*mult (under 0.20 cap)
        crisis = compute_size(_inputs(regime="crisis", drawdown_pct=0.0, recent_win_rate=0.0))
        up = compute_size(_inputs(regime="uptrend", drawdown_pct=0.0, recent_win_rate=0.0))
        if up.fraction > 0:
            ratio = crisis.fraction / up.fraction
            assert ratio == pytest.approx(1.50, rel=0.01)

    def test_crisis_capped_at_max_fraction(self) -> None:
        # Even in crisis with perfect params, must not exceed MAX_FRACTION
        result = compute_size(_inputs(
            regime="crisis",
            signal_confidence=1.0,
            recent_win_rate=0.9,
            recent_avg_win_pct=2.0,
            recent_avg_loss_pct=0.1,
            drawdown_pct=0.0,
        ))
        assert result.fraction <= MAX_FRACTION


class TestDrawdownCircuitBreaker:
    """Drawdown reduces size — severe drawdown triggers circuit breaker."""

    def test_drawdown_circuit_breaker(self) -> None:
        no_dd = compute_size(_inputs(drawdown_pct=0.0))
        heavy_dd = compute_size(_inputs(drawdown_pct=0.30))
        assert heavy_dd.size_usd < no_dd.size_usd

    def test_drawdown_10pct_applies_mult(self) -> None:
        # dd=0.10 → mult = max(0.2, 1 - 0.10*2) = 0.80
        result = compute_size(_inputs(drawdown_pct=0.10, regime="uptrend"))
        assert result.fraction > 0
        assert "10.0%" in result.reason

    def test_drawdown_floor_at_0_2(self) -> None:
        # dd >= 0.40 → floor at 0.2 mult (max(0.2, 1-0.4*2)=max(0.2,-0.2)=0.2)
        small_dd = compute_size(_inputs(drawdown_pct=0.40, regime="uptrend"))
        huge_dd = compute_size(_inputs(drawdown_pct=0.99, regime="uptrend"))
        # Both should be equal (floored at 0.2 mult)
        assert small_dd.fraction == pytest.approx(huge_dd.fraction, rel=0.01)


class TestMinSizeSkipSignal:
    """size_usd < MIN_SIZE_USD → skip (size=0, fraction=0)."""

    def test_min_size_skip_signal(self) -> None:
        # tiny cash → size would be < MIN_SIZE_USD
        result = compute_size(_inputs(
            cash_usd=200.0,
            regime="downtrend",
            signal_confidence=0.5,
            recent_win_rate=0.3,
            recent_avg_win_pct=0.3,
            recent_avg_loss_pct=0.5,
            drawdown_pct=0.30,
        ))
        assert result.size_usd == 0
        assert result.fraction == 0

    def test_min_size_threshold_constant(self) -> None:
        # Phase 2N: raised from $50 to $100 (Jin mandate — fee efficiency)
        assert MIN_SIZE_USD == 100.0


class TestColdStart:
    """recent_win_rate <= 0 or recent_avg_loss_pct <= 0 → 5% baseline kelly."""

    def test_cold_start_baseline(self) -> None:
        result = compute_size(_inputs(recent_win_rate=0.0, recent_avg_loss_pct=0.0))
        # kelly=0.05 baseline used — size > 0 for normal regime
        assert result.fraction > 0

    def test_cold_start_low_win_rate(self) -> None:
        # Only 2 trades (< 5) simulated via win_rate=0
        result = compute_size(_inputs(recent_win_rate=0.0))
        assert result.fraction > 0

    def test_cold_start_reason_has_kelly(self) -> None:
        result = compute_size(_inputs(recent_win_rate=0.0, recent_avg_loss_pct=0.0))
        assert "kelly=" in result.reason


class TestReasonFormat:
    """SizingOutput.reason must include all components."""

    def test_reason_includes_all_components(self) -> None:
        result = compute_size(_inputs())
        assert "kelly=" in result.reason
        assert "conf²=" in result.reason
        assert "regime[" in result.reason
        assert "dd[" in result.reason

    def test_output_dataclass_frozen(self) -> None:
        result = compute_size(_inputs())
        with pytest.raises(Exception):
            result.size_usd = 999  # type: ignore[misc]


class TestRegimeConstants:
    """REGIME_MULT values — North Star mandate."""

    def test_regime_mult_values(self) -> None:
        assert REGIME_MULT["crisis"] == 1.5
        assert REGIME_MULT["uptrend"] == 1.0
        assert REGIME_MULT["flat"] == 0.7
        assert REGIME_MULT["downtrend"] == 0.3

    def test_unknown_regime_defaults_to_flat(self) -> None:
        result = compute_size(_inputs(regime="unknown_regime"))
        flat_result = compute_size(_inputs(regime="flat"))
        assert result.fraction == pytest.approx(flat_result.fraction, rel=0.001)


# ── property-based test (P7 — Hypothesis) ─────────────────────────────────

@given(
    cash_usd=st.floats(min_value=100.0, max_value=1_000_000.0, allow_nan=False),
    signal_confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    recent_win_rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    recent_avg_win_pct=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
    recent_avg_loss_pct=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
    regime=st.sampled_from(["crisis", "uptrend", "flat", "downtrend", "weird"]),
    drawdown_pct=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
@settings(max_examples=300)
def test_property_fraction_within_bounds(
    cash_usd: float,
    signal_confidence: float,
    recent_win_rate: float,
    recent_avg_win_pct: float,
    recent_avg_loss_pct: float,
    regime: str,
    drawdown_pct: float,
) -> None:
    """Property: fraction always in [0, MAX_FRACTION]."""
    result = compute_size(SizingInputs(
        cash_usd=cash_usd,
        signal_confidence=signal_confidence,
        recent_win_rate=recent_win_rate,
        recent_avg_win_pct=recent_avg_win_pct,
        recent_avg_loss_pct=recent_avg_loss_pct,
        regime=regime,
        drawdown_pct=drawdown_pct,
    ))
    assert 0.0 <= result.fraction <= MAX_FRACTION
    assert result.size_usd >= 0
    # size_usd must be 0 or >= MIN_SIZE_USD (never "partial" skip)
    if result.size_usd > 0:
        assert result.size_usd >= MIN_SIZE_USD
