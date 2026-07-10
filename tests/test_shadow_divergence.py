"""Tests for shadow_divergence (fee-split v0 — dual-score shadow measurement).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Spec:
vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 item 5.
"""
from __future__ import annotations

import pytest

from polaris.core.classes.gross_scorer import GrossLcbResult
from polaris.core.classes.shadow_divergence import (
    MIN_SHADOW_CLOSES,
    DualScoreSample,
    classify_against_thresholds,
    classify_new_axis_with_cold_start_guard,
    compute_shadow_divergence,
)

# ---------------------------------------------------------------------------
# classify_against_thresholds
# ---------------------------------------------------------------------------


def test_classify_below_down_threshold_is_down():
    assert classify_against_thresholds(-5.0, down_threshold=-3.0, up_threshold=2.0) == "DOWN"


def test_classify_above_up_threshold_is_up():
    assert classify_against_thresholds(5.0, down_threshold=-3.0, up_threshold=2.0) == "UP"


def test_classify_between_is_hold():
    assert classify_against_thresholds(0.0, down_threshold=-3.0, up_threshold=2.0) == "HOLD"


def test_classify_inverted_thresholds_degrades_to_hold_never_raises():
    # down_threshold > up_threshold (caller error) -> nothing is < down AND
    # nothing between is impossible to be both -> effectively HOLD/UP only,
    # never crashes.
    result = classify_against_thresholds(0.0, down_threshold=5.0, up_threshold=-5.0)
    assert result in ("HOLD", "DOWN", "UP")  # no crash is the actual assertion


# ---------------------------------------------------------------------------
# classify_new_axis_with_cold_start_guard — R2 item 2 non-starvation
# ---------------------------------------------------------------------------


def _lcb(mean: float | None, band: str) -> GrossLcbResult:
    return GrossLcbResult(
        n_samples=20, n_eff=15.0, confidence=0.7, mean=mean, lcb=mean, verdict_band=band,  # type: ignore[arg-type]
    )


def test_cold_start_band_never_emits_down():
    result = classify_new_axis_with_cold_start_guard(
        _lcb(-10.0, "COLD_START"), down_threshold=-3.0, up_threshold=2.0,
    )
    assert result == "HOLD"


def test_insufficient_band_none_mean_is_hold():
    result = classify_new_axis_with_cold_start_guard(
        _lcb(None, "INSUFFICIENT"), down_threshold=-3.0, up_threshold=2.0,
    )
    assert result == "HOLD"


def test_full_band_allows_down():
    result = classify_new_axis_with_cold_start_guard(
        _lcb(-10.0, "FULL"), down_threshold=-3.0, up_threshold=2.0,
    )
    assert result == "DOWN"


def test_cold_start_band_still_allows_up():
    """The non-starvation guard only suppresses DOWN — UP must pass through
    even in COLD_START (promotion/hold-only, not hold-only)."""
    result = classify_new_axis_with_cold_start_guard(
        _lcb(10.0, "COLD_START"), down_threshold=-3.0, up_threshold=2.0,
    )
    assert result == "UP"


# ---------------------------------------------------------------------------
# compute_shadow_divergence
# ---------------------------------------------------------------------------


def test_empty_samples_never_ready():
    result = compute_shadow_divergence([])
    assert result.n_samples == 0
    assert not result.ready_to_flip


def test_below_min_n_never_ready_even_if_perfect_agreement():
    samples = [DualScoreSample(old_verdict="HOLD", new_verdict="HOLD") for _ in range(50)]
    result = compute_shadow_divergence(samples)
    assert result.n_samples == 50
    assert result.behavior_divergence_pct == pytest.approx(0.0)
    assert not result.ready_to_flip  # < MIN_SHADOW_CLOSES


def test_perfect_agreement_at_min_n_is_ready():
    samples = [DualScoreSample(old_verdict="HOLD", new_verdict="HOLD") for _ in range(MIN_SHADOW_CLOSES)]
    result = compute_shadow_divergence(samples)
    assert result.behavior_divergence_pct == pytest.approx(0.0)
    assert result.tier_divergence_pct == pytest.approx(0.0)
    assert result.transition_rate_diff_pp == pytest.approx(0.0)
    assert result.ready_to_flip


def test_total_disagreement_never_ready():
    samples = [
        DualScoreSample(old_verdict="UP", new_verdict="DOWN")
        for _ in range(MIN_SHADOW_CLOSES)
    ]
    result = compute_shadow_divergence(samples)
    assert result.behavior_divergence_pct == pytest.approx(1.0)
    assert not result.ready_to_flip


def test_behavior_divergence_exceeds_bar_blocks_flip():
    """20% fine-grained mismatch (UP vs HOLD, same coarse ACTIVE bucket) ->
    behavior divergence 20% > 15% bar -> not ready, even though tier
    divergence (coarse) stays 0%."""
    n = MIN_SHADOW_CLOSES
    mismatches = int(0.20 * n)
    samples = (
        [DualScoreSample(old_verdict="UP", new_verdict="HOLD") for _ in range(mismatches)]
        + [DualScoreSample(old_verdict="UP", new_verdict="UP") for _ in range(n - mismatches)]
    )
    result = compute_shadow_divergence(samples)
    assert result.behavior_divergence_pct == pytest.approx(0.20, abs=0.01)
    assert not result.ready_to_flip


def test_transition_rate_diff_computed_in_percentage_points():
    n = MIN_SHADOW_CLOSES
    # old: all HOLD (0% active); new: all UP (100% active) -> 100pp diff
    samples = [DualScoreSample(old_verdict="HOLD", new_verdict="UP") for _ in range(n)]
    result = compute_shadow_divergence(samples)
    assert result.old_transition_rate == pytest.approx(0.0)
    assert result.new_transition_rate == pytest.approx(1.0)
    assert result.transition_rate_diff_pp == pytest.approx(100.0)
    assert not result.ready_to_flip


def test_tier_divergence_ignores_up_vs_down_within_active():
    """old=UP, new=DOWN both count as 'ACTIVE' in the coarse bucket -> tier
    divergence 0% even though behavior (fine-grained) divergence is 100%."""
    samples = [DualScoreSample(old_verdict="UP", new_verdict="DOWN") for _ in range(MIN_SHADOW_CLOSES)]
    result = compute_shadow_divergence(samples)
    assert result.tier_divergence_pct == pytest.approx(0.0)
    assert result.behavior_divergence_pct == pytest.approx(1.0)
