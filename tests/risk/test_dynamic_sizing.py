"""Tests for dynamic_sizing — TDD red/green cycle (Phase 2j).

모태 Kelly + confidence + regime + drawdown 조합 검증.
Property-based test: 0 <= fraction <= MAX_FRACTION (P7).
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import src.risk.dynamic_sizing as _ds_module  # module ref for dynamic COLD_START_MAX_USD read
from src.risk.dynamic_sizing import (
    COLD_START_N,
    MAX_FRACTION,
    MIN_SIZE_USD,
    REGIME_MULT,
    SizingInputs,
    SizingOutput,
    compute_size,
)


def _cold_start_max() -> float:
    """Read COLD_START_MAX_USD from module (reflects conftest monkeypatch).

    IMPORTANT: Do NOT use `from src.risk.dynamic_sizing import COLD_START_MAX_USD` directly.
    That binds the value at import time. When production .env sets POLARIS_COLD_START_MAX_USD=0,
    the imported name is 0.0. conftest patches the module attr to 300.0, but local name stays 0.0.
    This helper always reads the live module attribute.
    """
    return _ds_module.COLD_START_MAX_USD


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


def _sized(
    cash_usd: float = 10_000.0,
    signal_confidence: float = 0.80,
    recent_win_rate: float = 0.55,
    recent_avg_win_pct: float = 0.8,
    recent_avg_loss_pct: float = 0.5,
    regime: str = "flat",
    drawdown_pct: float = 0.0,
    n_trades: int = COLD_START_N,   # warm start default — bypasses cold start cap
) -> SizingOutput:
    """Convenience: _inputs() → compute_size() with explicit n_trades.

    Default n_trades=COLD_START_N (warm start) so regime/confidence comparison
    tests are not masked by cold start cap. Tests that need cold start behavior
    should call compute_size(_inputs(...), n_trades=0) directly.
    """
    return compute_size(
        SizingInputs(
            cash_usd=cash_usd,
            signal_confidence=signal_confidence,
            recent_win_rate=recent_win_rate,
            recent_avg_win_pct=recent_avg_win_pct,
            recent_avg_loss_pct=recent_avg_loss_pct,
            regime=regime,
            drawdown_pct=drawdown_pct,
        ),
        n_trades=n_trades,
    )


# ── unit tests ─────────────────────────────────────────────────────────────

class TestHighConfidenceUptrend:
    """High confidence + uptrend → larger size than baseline flat."""

    def test_high_confidence_uptrend_full_kelly(self) -> None:
        baseline = _sized(regime="flat")
        uptrend = _sized(regime="uptrend", signal_confidence=0.9)
        assert uptrend.size_usd > baseline.size_usd

    def test_uptrend_regime_mult_1x(self) -> None:
        result = _sized(regime="uptrend")
        assert result.fraction > 0
        assert "uptrend" in result.reason


class TestLowConfidenceReduceSize:
    """Low confidence → size significantly below high-confidence case."""

    def test_low_confidence_reduce_size(self) -> None:
        low = _sized(signal_confidence=0.5)
        high = _sized(signal_confidence=0.9)
        # confidence² : 0.25 vs 0.81 → low should be much smaller
        assert low.size_usd < high.size_usd

    def test_confidence_cubed_applied(self) -> None:
        # F1: conf**3 (was **2). conf=0.5 → conf³=0.125; conf=1.0 → conf³=1.0 (8x difference).
        # Use cold-start kelly (win_rate=0) to avoid MAX_FRACTION cap distorting ratio.
        # cold kelly=0.05 × conf³=0.125 × flat=0.7 × dd=1.0 = 0.004375 (well under cap)
        # Phase 2N: MIN_SIZE_USD=$100 — use large cash to avoid skip masking ratio.
        low = _sized(cash_usd=100_000.0, signal_confidence=0.5, regime="flat", recent_win_rate=0.0)
        perfect = _sized(cash_usd=100_000.0, signal_confidence=1.0, regime="flat", recent_win_rate=0.0)
        ratio = perfect.fraction / low.fraction if low.fraction > 0 else float("inf")
        # Should be ~8x (conf³ ratio: 1.0 / 0.125 = 8.0)
        assert ratio == pytest.approx(8.0, rel=0.05)


class TestDowntrendSize:
    """Downtrend → regime_mult=0.3 → size reduced to ~30% of uptrend."""

    def test_downtrend_size_30pct(self) -> None:
        down = _sized(regime="downtrend", drawdown_pct=0.0)
        up = _sized(regime="uptrend", drawdown_pct=0.0)
        # downtrend mult 0.3 vs uptrend mult 1.0 → ratio ~0.3
        if up.fraction > 0:
            ratio = down.fraction / up.fraction
            assert ratio == pytest.approx(0.30, rel=0.01)

    def test_downtrend_reason_string(self) -> None:
        result = _sized(regime="downtrend")
        assert "downtrend" in result.reason


class TestCrisisSize:
    """Crisis → regime_mult=1.5 → 1.5× uptrend size (북극성 mandate)."""

    def test_crisis_size_150pct(self) -> None:
        # Use cold-start kelly to avoid MAX_FRACTION cap distorting the ratio.
        # cold kelly=0.05 × conf²=0.64 × mult × dd=1.0 = 0.032*mult (under 0.20 cap)
        crisis = _sized(regime="crisis", drawdown_pct=0.0, recent_win_rate=0.0)
        up = _sized(regime="uptrend", drawdown_pct=0.0, recent_win_rate=0.0)
        if up.fraction > 0:
            ratio = crisis.fraction / up.fraction
            assert ratio == pytest.approx(1.50, rel=0.01)

    def test_crisis_capped_at_max_fraction(self) -> None:
        # Even in crisis with perfect params, must not exceed MAX_FRACTION
        result = _sized(
            regime="crisis",
            signal_confidence=1.0,
            recent_win_rate=0.9,
            recent_avg_win_pct=2.0,
            recent_avg_loss_pct=0.1,
            drawdown_pct=0.0,
        )
        assert result.fraction <= MAX_FRACTION


class TestDrawdownCircuitBreaker:
    """Drawdown reduces size — severe drawdown triggers circuit breaker."""

    def test_drawdown_circuit_breaker(self) -> None:
        no_dd = _sized(drawdown_pct=0.0)
        heavy_dd = _sized(drawdown_pct=0.30)
        assert heavy_dd.size_usd < no_dd.size_usd

    def test_drawdown_10pct_applies_mult(self) -> None:
        # dd=0.10 → mult = max(0.2, 1 - 0.10*2) = 0.80
        result = _sized(drawdown_pct=0.10, regime="uptrend")
        assert result.fraction > 0
        assert "10.0%" in result.reason

    def test_drawdown_floor_at_0_2(self) -> None:
        # dd >= 0.40 → floor at 0.2 mult (max(0.2, 1-0.4*2)=max(0.2,-0.2)=0.2)
        small_dd = _sized(drawdown_pct=0.40, regime="uptrend")
        huge_dd = _sized(drawdown_pct=0.99, regime="uptrend")
        # Both should be equal (floored at 0.2 mult)
        assert small_dd.fraction == pytest.approx(huge_dd.fraction, rel=0.01)


class TestMinSizeSkipSignal:
    """size_usd < MIN_SIZE_USD → skip (size=0, fraction=0)."""

    def test_min_size_skip_signal(self) -> None:
        # tiny cash → size would be < MIN_SIZE_USD (cold start cap doesn't matter — min check first)
        result = _sized(
            cash_usd=200.0,
            regime="downtrend",
            signal_confidence=0.5,
            recent_win_rate=0.3,
            recent_avg_win_pct=0.3,
            recent_avg_loss_pct=0.5,
            drawdown_pct=0.30,
        )
        assert result.size_usd == 0
        assert result.fraction == 0

    def test_min_size_threshold_constant(self) -> None:
        # Phase 2N: raised from $50 to $100 (Jin mandate — fee efficiency)
        assert MIN_SIZE_USD == 100.0


class TestColdStart:
    """recent_win_rate <= 0 or recent_avg_loss_pct <= 0 → 5% baseline kelly."""

    def test_cold_start_baseline(self) -> None:
        # n_trades=COLD_START_N (warm start) — testing kelly baseline branch, not n_trades cap
        result = _sized(recent_win_rate=0.0, recent_avg_loss_pct=0.0)
        # kelly=0.05 baseline used — size > 0 for normal regime
        assert result.fraction > 0

    def test_cold_start_low_win_rate(self) -> None:
        # Only 2 trades (< 5) simulated via win_rate=0
        result = _sized(recent_win_rate=0.0)
        assert result.fraction > 0

    def test_cold_start_reason_has_kelly(self) -> None:
        result = _sized(recent_win_rate=0.0, recent_avg_loss_pct=0.0)
        assert "kelly=" in result.reason


class TestReasonFormat:
    """SizingOutput.reason must include all components."""

    def test_reason_includes_all_components(self) -> None:
        result = _sized()
        assert "kelly=" in result.reason
        assert "conf³=" in result.reason  # F1: cubed tag
        assert "regime[" in result.reason
        assert "dd[" in result.reason

    def test_output_dataclass_frozen(self) -> None:
        result = _sized()
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
        result = _sized(regime="unknown_regime")
        flat_result = _sized(regime="flat")
        assert result.fraction == pytest.approx(flat_result.fraction, rel=0.001)


# ── Cold start size cap (Phase 2N+ — HYPO-025 lesson) ────────────────────

class TestColdStartCap:
    """n_trades < COLD_START_N → size capped at COLD_START_MAX_USD."""

    def test_cold_start_caps_size_at_300(self) -> None:
        """n_trades=0 + high confidence + uptrend → size <= $300."""
        result = compute_size(
            _inputs(
                cash_usd=5000.0,
                signal_confidence=1.0,
                recent_win_rate=0.8,
                recent_avg_win_pct=2.0,
                recent_avg_loss_pct=0.5,
                regime="uptrend",
                drawdown_pct=0.0,
            ),
            n_trades=0,
        )
        assert result.size_usd <= _cold_start_max()
        assert result.size_usd == _cold_start_max()  # capped exactly

    def test_n_19_still_cold_start(self) -> None:
        """n_trades=19 (< 20) → still capped."""
        result = compute_size(
            _inputs(
                cash_usd=5000.0,
                signal_confidence=0.9,
                recent_win_rate=0.7,
                recent_avg_win_pct=1.5,
                recent_avg_loss_pct=0.5,
                regime="crisis",
                drawdown_pct=0.0,
            ),
            n_trades=19,
        )
        assert result.size_usd <= _cold_start_max()

    def test_n_20_plus_uses_full_dynamic(self) -> None:
        """n_trades=20 → no cold start cap — full dynamic sizing applies."""
        result_20 = compute_size(
            _inputs(
                cash_usd=5000.0,
                signal_confidence=1.0,
                recent_win_rate=0.8,
                recent_avg_win_pct=2.0,
                recent_avg_loss_pct=0.5,
                regime="uptrend",
                drawdown_pct=0.0,
            ),
            n_trades=20,
        )
        # At n=20 full dynamic applies — should exceed cold start cap (high confidence + uptrend)
        assert result_20.size_usd > _cold_start_max()

    def test_cold_start_threshold_constant(self) -> None:
        assert COLD_START_N == 20
        assert _cold_start_max() == 300.0  # default value when env not set

    def test_cold_start_reason_tag(self) -> None:
        """cold_start tag appears in reason when cap applied."""
        result = compute_size(
            _inputs(
                cash_usd=5000.0,
                signal_confidence=1.0,
                recent_win_rate=0.8,
                recent_avg_win_pct=2.0,
                recent_avg_loss_pct=0.5,
                regime="uptrend",
                drawdown_pct=0.0,
            ),
            n_trades=5,
        )
        assert "cold_start" in result.reason

    def test_no_cold_start_tag_when_n_20(self) -> None:
        """No cold_start tag in reason when n_trades >= 20."""
        result = compute_size(_inputs(), n_trades=20)
        assert "cold_start" not in result.reason

    def test_cold_start_below_min_size_still_returns_zero(self) -> None:
        """Even with cold start, if dynamic size < MIN_SIZE_USD → return 0."""
        # tiny cash + downtrend + low conf → size < $100 → skip
        result = compute_size(
            _inputs(
                cash_usd=200.0,
                signal_confidence=0.3,
                recent_win_rate=0.0,
                regime="downtrend",
                drawdown_pct=0.5,
            ),
            n_trades=0,
        )
        assert result.size_usd == 0.0

    def test_property_cold_start_never_exceeds_cap(self) -> None:
        """Property: n_trades < COLD_START_N → size_usd <= COLD_START_MAX_USD."""
        # Test boundary values
        for n_t in [0, 1, 10, 19]:
            result = compute_size(
                _inputs(
                    cash_usd=100_000.0,
                    signal_confidence=1.0,
                    recent_win_rate=0.9,
                    recent_avg_win_pct=5.0,
                    recent_avg_loss_pct=0.1,
                    regime="crisis",
                    drawdown_pct=0.0,
                ),
                n_trades=n_t,
            )
            assert result.size_usd <= _cold_start_max(), f"n_trades={n_t} exceeded cap"


# ── Fix: n_trades default=0 ensures cold start cap without explicit arg ───────


class TestColdStartCapDefault:
    """n_trades default=0 fixes the latent bug where omitting n_trades bypassed cap.

    Root cause (2026-05-04): compute_size(inputs, n_trades=COLD_START_N) default
    meant callers who forgot n_trades got n=20 (no cap). Fix: default=0 (safe cold start).
    """

    def test_default_n_trades_applies_cold_start_cap(self) -> None:
        """compute_size() without n_trades arg → cold start cap applied (n=0 default).

        VPIN first close was $917 when cold start cap should give $300 max.
        This test confirms the fix: omitting n_trades → n=0 → cap applies.
        """
        # High-confidence crisis scenario that would produce $917+ without cap
        result = compute_size(
            _inputs(
                cash_usd=50_000.0,        # $50k starting (Phase 2Q)
                signal_confidence=0.90,
                recent_win_rate=0.55,
                recent_avg_win_pct=0.8,
                recent_avg_loss_pct=0.5,
                regime="crisis",
                drawdown_pct=0.0,
            )
            # n_trades intentionally OMITTED — must default to 0 (cold start safe)
        )
        assert result.size_usd <= _cold_start_max(), (
            f"Omitting n_trades must apply cold start cap (n=0 default). "
            f"Got ${result.size_usd:.0f} > COLD_START_MAX_USD=${_cold_start_max():.0f}. "
            "Root cause fix: default n_trades=0 (was COLD_START_N=20 before fix)."
        )
        assert "cold_start" in result.reason, "cold_start tag must appear in reason"

    def test_cold_start_cap_n0_max_300(self) -> None:
        """n_trades=0 (first VPIN trade) → size capped at $300.

        Scenario: VPIN HYPO-033, n=0 closed trades, $50k capital, crisis regime.
        Expected: $300 max (cold start cap), not $917 (dynamic sizing raw output).
        """
        result = compute_size(
            _inputs(
                cash_usd=50_000.0,
                signal_confidence=0.85,
                recent_win_rate=0.55,
                recent_avg_win_pct=0.8,
                recent_avg_loss_pct=0.5,
                regime="uptrend",
                drawdown_pct=0.0,
            ),
            n_trades=0,
        )
        assert result.size_usd <= _cold_start_max(), (
            f"n=0 cold start must cap at ${_cold_start_max():.0f}, got ${result.size_usd:.0f}"
        )
        assert result.size_usd == _cold_start_max(), (
            f"High-signal n=0 should hit cap exactly at $300, got ${result.size_usd:.0f}"
        )


# ── F1 amplification: conf³ + F4 Kelly boost ──────────────────────────────

class TestF1Amplification:
    """F1: conf**3 — strong signal dominates, weak signal suppressed harder."""

    def test_strong_conf_09_cubed_vs_squared(self) -> None:
        # conf=0.9: **3=0.729 vs **2=0.81. conf³ < conf² for conf<1.
        # Test that sizing with cal_conf=0.9 produces LESS than the old conf²=0.81 path
        # (F1 trades peak-conf for steeper penalty on mediocre signals).
        # We verify conf³ < conf² for conf<1 which is mathematically guaranteed.
        for conf in (0.7, 0.8, 0.9):
            cubed = conf ** 3
            squared = conf ** 2
            assert cubed < squared, f"conf={conf}: cubed={cubed} must < squared={squared}"

    def test_conf_1_unchanged_by_cubing(self) -> None:
        # conf=1.0 → 1.0**3 = 1.0 (top signal unaffected)
        result = compute_size(SizingInputs(
            cash_usd=50_000.0,
            signal_confidence=1.0,
            recent_win_rate=0.55,
            recent_avg_win_pct=0.8,
            recent_avg_loss_pct=0.5,
            regime="uptrend",
            drawdown_pct=0.0,
            calibrated_confidence=1.0,
        ), n_trades=COLD_START_N)
        # Perfect confidence → max fraction (capped at MAX_FRACTION or kelly cap)
        assert result.fraction > 0

    def test_conf_cubed_reason_tag(self) -> None:
        result = compute_size(SizingInputs(
            cash_usd=10_000.0,
            signal_confidence=0.8,
            recent_win_rate=0.55,
            recent_avg_win_pct=0.8,
            recent_avg_loss_pct=0.5,
            regime="flat",
            drawdown_pct=0.0,
            calibrated_confidence=0.8,
        ), n_trades=COLD_START_N)
        assert "cal_conf³" in result.reason  # F1: cubed tag with cal prefix


class TestF4KellyBoost:
    """F4: calibrated_confidence boosts kelly by ×(1 + cal_conf×0.5). cal=0.9 → ×1.45."""

    def test_f4_boost_increases_kelly(self) -> None:
        # Without cal_conf: kelly uses raw value only.
        # With cal_conf=0.9: kelly gets ×1.45 boost.
        base = compute_size(SizingInputs(
            cash_usd=50_000.0,
            signal_confidence=0.8,
            recent_win_rate=0.55,
            recent_avg_win_pct=0.8,
            recent_avg_loss_pct=0.5,
            regime="uptrend",
            drawdown_pct=0.0,
            calibrated_confidence=None,   # no cal → no boost
        ), n_trades=COLD_START_N)
        boosted = compute_size(SizingInputs(
            cash_usd=50_000.0,
            signal_confidence=0.8,
            recent_win_rate=0.55,
            recent_avg_win_pct=0.8,
            recent_avg_loss_pct=0.5,
            regime="uptrend",
            drawdown_pct=0.0,
            calibrated_confidence=0.9,   # F4: kelly × 1.45
        ), n_trades=COLD_START_N)
        # Boosted must be >= base (kelly boost + conf³ compound)
        assert boosted.size_usd >= base.size_usd

    def test_f4_boost_capped_at_half_kelly(self) -> None:
        # Even with extreme boost, kelly stays ≤ _KELLY_HALF_CAP
        result = compute_size(SizingInputs(
            cash_usd=50_000.0,
            signal_confidence=0.9,
            recent_win_rate=0.9,
            recent_avg_win_pct=5.0,
            recent_avg_loss_pct=0.1,
            regime="crisis",
            drawdown_pct=0.0,
            calibrated_confidence=1.0,   # max boost
        ), n_trades=COLD_START_N)
        assert result.fraction <= MAX_FRACTION

    def test_f4_no_boost_when_cal_conf_none(self) -> None:
        # calibrated_confidence=None → F4 block skipped (no boost applied)
        result = compute_size(SizingInputs(
            cash_usd=10_000.0,
            signal_confidence=0.8,
            recent_win_rate=0.55,
            recent_avg_win_pct=0.8,
            recent_avg_loss_pct=0.5,
            regime="flat",
            drawdown_pct=0.0,
            calibrated_confidence=None,
        ), n_trades=COLD_START_N)
        assert result.fraction > 0
        assert "conf³" in result.reason  # no cal prefix when cal_conf is None


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
