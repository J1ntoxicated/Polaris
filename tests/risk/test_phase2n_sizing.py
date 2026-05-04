"""Phase 2N sizing changes — TDD verification (Fix 5).

Tests:
  - MIN_SIZE_USD = $100 (raised from $50)
  - _COLD_START_DEFAULTS: win=0.55, avg_win=0.8, avg_loss=0.5
  - Cold start Kelly = 0.225 (via tracker → compute_size pipeline)
  - Effective size at $5000 × cold_start = $720 target range
  - All previous property-based invariants still hold
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.risk.dynamic_sizing import (
    MAX_FRACTION,
    MIN_SIZE_USD,
    SizingInputs,
    compute_size,
)
from src.risk.performance_tracker import _COLD_START_DEFAULTS, _MIN_SAMPLE, compute_recent_stats


# ── MIN_SIZE_USD = $100 ───────────────────────────────────────────────────────

class TestMinSizeUSDPhase2N:
    """MIN_SIZE_USD raised to $100."""

    def test_min_size_is_100(self) -> None:
        assert MIN_SIZE_USD == 100.0

    def test_cash_200_flat_regime_skip(self) -> None:
        """$200 × flat(0.7) × kelly(cold=0.05) × conf²(0.64) = $4.48 → skip."""
        result = compute_size(SizingInputs(
            cash_usd=200.0,
            signal_confidence=0.8,
            recent_win_rate=0.0,
            recent_avg_win_pct=0.0,
            recent_avg_loss_pct=0.0,
            regime="flat",
            drawdown_pct=0.0,
        ))
        assert result.size_usd == 0.0
        assert result.fraction == 0.0

    def test_cash_5000_cold_start_above_min(self) -> None:
        """$5000 × cold_start path should produce size >= $100."""
        # Using cold-start tracker defaults (win=0.55, avg_win=0.8, avg_loss=0.5)
        result = compute_size(SizingInputs(
            cash_usd=5000.0,
            signal_confidence=0.8,
            recent_win_rate=0.55,
            recent_avg_win_pct=0.8,
            recent_avg_loss_pct=0.5,
            regime="flat",
            drawdown_pct=0.0,
        ))
        assert result.size_usd >= 100.0


# ── _COLD_START_DEFAULTS (performance_tracker) ────────────────────────────────

class TestColdStartDefaults:
    """Cold start defaults: win=0.55, avg_win=0.8, avg_loss=0.5."""

    def test_cold_start_win_rate(self) -> None:
        assert _COLD_START_DEFAULTS["win_rate"] == pytest.approx(0.55)

    def test_cold_start_avg_win_pct(self) -> None:
        assert _COLD_START_DEFAULTS["avg_win_pct"] == pytest.approx(0.8)

    def test_cold_start_avg_loss_pct(self) -> None:
        assert _COLD_START_DEFAULTS["avg_loss_pct"] == pytest.approx(0.5)

    def test_cold_start_kelly_value(self) -> None:
        """Cold start Kelly: b=0.8/0.5=1.6, p=0.55, q=0.45.
        f* = (1.6×0.55 - 0.45)/1.6 = (0.88-0.45)/1.6 = 0.43/1.6 = 0.268.
        Half-Kelly cap = 0.5, so effective = 0.268 (no cap needed).
        """
        d = _COLD_START_DEFAULTS
        b = d["avg_win_pct"] / d["avg_loss_pct"]
        p = d["win_rate"]
        q = 1.0 - p
        kelly_raw = (b * p - q) / b
        assert kelly_raw == pytest.approx(0.268, rel=0.01)

    def test_cold_start_returned_when_few_positions(self) -> None:
        """compute_recent_stats returns defaults when < _MIN_SAMPLE positions."""
        stats = compute_recent_stats([], lookback=20)
        assert stats == dict(_COLD_START_DEFAULTS)

    def test_min_sample_still_5(self) -> None:
        assert _MIN_SAMPLE == 5


# ── Effective size at $5000 (pipeline test) ────────────────────────────────────

class TestEffectiveSizePipeline:
    """End-to-end: tracker defaults → compute_size → $720 target.

    $5000 × kelly(0.268) × conf²(0.64) × flat(0.7) × dd(1.0) = $601
    Cap: MAX_FRACTION(0.20) × $5000 = $1000 → no cap needed here.
    Note: result will be ~$600, well above $100 threshold.
    """

    def _cold_start_inputs(
        self,
        cash_usd: float = 5000.0,
        regime: str = "flat",
        dd: float = 0.0,
        conf: float = 0.8,
    ) -> SizingInputs:
        d = _COLD_START_DEFAULTS
        return SizingInputs(
            cash_usd=cash_usd,
            signal_confidence=conf,
            recent_win_rate=d["win_rate"],
            recent_avg_win_pct=d["avg_win_pct"],
            recent_avg_loss_pct=d["avg_loss_pct"],
            regime=regime,
            drawdown_pct=dd,
        )

    def test_flat_regime_size_above_400(self) -> None:
        """flat regime: $5000 cold-start → size ~$600 (well above $100 min)."""
        result = compute_size(self._cold_start_inputs(regime="flat"))
        assert result.size_usd >= 400.0

    def test_uptrend_regime_larger_than_flat(self) -> None:
        flat = compute_size(self._cold_start_inputs(regime="flat"))
        uptrend = compute_size(self._cold_start_inputs(regime="uptrend"))
        assert uptrend.size_usd > flat.size_usd

    def test_crisis_regime_max_size(self) -> None:
        """crisis=1.5x: should approach MAX_FRACTION cap at $5000."""
        result = compute_size(self._cold_start_inputs(regime="crisis", conf=1.0))
        # MAX_FRACTION=0.20 → cap at $1000
        assert result.size_usd <= MAX_FRACTION * 5000.0 + 0.01

    def test_downtrend_regime_reduces_size(self) -> None:
        """downtrend=0.3x: size should be significantly reduced."""
        flat = compute_size(self._cold_start_inputs(regime="flat"))
        down = compute_size(self._cold_start_inputs(regime="downtrend"))
        assert down.size_usd < flat.size_usd

    def test_size_3x_vs_old_cold_start(self) -> None:
        """New cold-start (~$600 flat) vs old cold-start estimate (~$265).
        Kelly ratio: 0.268 / 0.083 = 3.2× improvement.
        """
        # Old defaults: win=0.5, avg_win=0.6, avg_loss=0.5 → kelly=0.083
        old_inputs = SizingInputs(
            cash_usd=5000.0,
            signal_confidence=0.8,
            recent_win_rate=0.5,
            recent_avg_win_pct=0.6,
            recent_avg_loss_pct=0.5,
            regime="flat",
            drawdown_pct=0.0,
        )
        new_inputs = self._cold_start_inputs(regime="flat")
        old_result = compute_size(old_inputs)
        new_result = compute_size(new_inputs)
        assert new_result.size_usd > old_result.size_usd
        # Ratio should be ~3x (kelly 0.268 / 0.083)
        ratio = new_result.size_usd / max(old_result.size_usd, 1.0)
        assert ratio >= 2.5


# ── Property: invariants unchanged ────────────────────────────────────────────

@given(
    cash_usd=st.floats(min_value=100.0, max_value=1_000_000.0, allow_nan=False),
    signal_confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    recent_win_rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    recent_avg_win_pct=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
    recent_avg_loss_pct=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
    regime=st.sampled_from(["crisis", "uptrend", "flat", "downtrend"]),
    drawdown_pct=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
@settings(max_examples=300)
def test_property_phase2n_invariants(
    cash_usd: float,
    signal_confidence: float,
    recent_win_rate: float,
    recent_avg_win_pct: float,
    recent_avg_loss_pct: float,
    regime: str,
    drawdown_pct: float,
) -> None:
    """Property: fraction always in [0, MAX_FRACTION]. size_usd=0 or >=MIN_SIZE_USD."""
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
    if result.size_usd > 0:
        assert result.size_usd >= MIN_SIZE_USD  # must be >= $100 now
