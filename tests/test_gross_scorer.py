"""Tests for gross_scorer (fee-split v0 — winsorized weighted t-LCB).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Pure
math module — no DB, no sizing/9-stack touch. Spec source:
vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 items 2-3.
"""
from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from polaris.core.classes.gross_scorer import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    N_EFF_CAP,
    N_EFF_CONFIDENCE_FULL,
    N_EFF_MIN_FLOOR,
    GrossEdgeSample,
    compute_gross_lcb,
    confidence_for_n_eff,
    effective_sample_size,
    time_decay_weight,
    weighted_percentile,
    winsorize,
)

# ---------------------------------------------------------------------------
# winsorize
# ---------------------------------------------------------------------------


def test_winsorize_clips_extreme_outlier():
    values = [1.0, 2.0, 3.0, 2.0, 1.0, 100_000.0]
    out = winsorize(values)
    assert max(out) < 100_000.0
    # non-outlier values pass through unchanged
    assert out[:5] == values[:5]


def test_winsorize_empty_list():
    assert winsorize([]) == []


def test_winsorize_zero_mad_no_op():
    """All-identical values -> MAD=0 -> no clipping (avoid div-by-zero bound
    collapsing every value to the median)."""
    values = [5.0, 5.0, 5.0]
    assert winsorize(values) == values


def test_winsorize_uniform_values_unaffected():
    values = [10.0, 11.0, 9.0, 10.5, 9.5]
    out = winsorize(values)
    assert out == pytest.approx(values)


# ---------------------------------------------------------------------------
# time_decay_weight
# ---------------------------------------------------------------------------


def test_time_decay_weight_zero_age_is_one():
    assert time_decay_weight(0.0, 3600.0) == pytest.approx(1.0)


def test_time_decay_weight_decreases_with_age():
    w_recent = time_decay_weight(100.0, 3600.0)
    w_old = time_decay_weight(10_000.0, 3600.0)
    assert 0.0 < w_old < w_recent <= 1.0


def test_time_decay_weight_matches_h_characteristic_time():
    """At age == H, weight == exp(-1) (the defining property of H)."""
    h = 3600.0
    assert time_decay_weight(h, h) == pytest.approx(math.exp(-1.0))


def test_time_decay_weight_negative_age_clamped():
    """A future-dated close (negative age) never explodes weight > 1.0."""
    assert time_decay_weight(-500.0, 3600.0) == pytest.approx(1.0)


def test_time_decay_weight_nonpositive_h_fails_open():
    assert time_decay_weight(1000.0, 0.0) == 1.0
    assert time_decay_weight(1000.0, -10.0) == 1.0


# ---------------------------------------------------------------------------
# effective_sample_size (Kish N_eff)
# ---------------------------------------------------------------------------


def test_n_eff_uniform_weights_equals_count():
    weights = [1.0] * 10
    assert effective_sample_size(weights) == pytest.approx(10.0)


def test_n_eff_unequal_weights_less_than_count():
    """Unequal weights always shrink N_eff below the raw count (Kish's
    design-effect inequality) — proves recency-decay genuinely discounts
    stale samples rather than counting them at face value."""
    weights = [1.0, 0.5, 0.1, 0.01]
    n_eff = effective_sample_size(weights)
    assert n_eff < len(weights)
    assert n_eff > 0.0


def test_n_eff_empty_is_zero():
    assert effective_sample_size([]) == 0.0


def test_n_eff_all_zero_weights_is_zero():
    assert effective_sample_size([0.0, 0.0, 0.0]) == 0.0


@given(st.lists(st.floats(min_value=1e-6, max_value=1.0), min_size=1, max_size=50))
def test_n_eff_never_exceeds_raw_count(weights: list[float]) -> None:
    assert effective_sample_size(weights) <= len(weights) + 1e-6


# ---------------------------------------------------------------------------
# confidence_for_n_eff — 60% -> 80% ramp over N_eff 12 -> 20
# ---------------------------------------------------------------------------


def test_confidence_below_floor_is_none():
    assert confidence_for_n_eff(11.9) is None
    assert confidence_for_n_eff(0.0) is None


def test_confidence_at_floor_is_min():
    assert confidence_for_n_eff(N_EFF_MIN_FLOOR) == pytest.approx(CONFIDENCE_MIN)


def test_confidence_at_full_is_max():
    assert confidence_for_n_eff(N_EFF_CONFIDENCE_FULL) == pytest.approx(CONFIDENCE_MAX)


def test_confidence_above_full_stays_flat_at_max():
    assert confidence_for_n_eff(N_EFF_CAP) == pytest.approx(CONFIDENCE_MAX)
    assert confidence_for_n_eff(1000.0) == pytest.approx(CONFIDENCE_MAX)


def test_confidence_ramp_midpoint():
    mid = (N_EFF_MIN_FLOOR + N_EFF_CONFIDENCE_FULL) / 2
    c = confidence_for_n_eff(mid)
    assert c == pytest.approx((CONFIDENCE_MIN + CONFIDENCE_MAX) / 2)


def test_confidence_ramp_monotonic():
    prev = CONFIDENCE_MIN
    for n in range(12, 21):
        c = confidence_for_n_eff(float(n))
        assert c is not None
        assert c >= prev
        prev = c


# ---------------------------------------------------------------------------
# weighted_percentile
# ---------------------------------------------------------------------------


def test_weighted_percentile_uniform_weights_matches_median():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    weights = [1.0] * 5
    assert weighted_percentile(values, weights, 0.5) == pytest.approx(3.0)


def test_weighted_percentile_empty_is_zero():
    assert weighted_percentile([], [], 0.5) == 0.0


def test_weighted_percentile_zero_total_weight_falls_back_to_median():
    values = [1.0, 2.0, 3.0]
    weights = [0.0, 0.0, 0.0]
    assert weighted_percentile(values, weights, 0.5) == pytest.approx(2.0)


def test_weighted_percentile_low_pct_favors_small_values():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    weights = [1.0] * 5
    assert weighted_percentile(values, weights, 0.2) <= 2.0


# ---------------------------------------------------------------------------
# compute_gross_lcb — end-to-end
# ---------------------------------------------------------------------------

_H_1H = 3600.0


def _mk(values: list[float], *, now_ts: int = 100_000, spacing: int = 60) -> list[GrossEdgeSample]:
    return [
        GrossEdgeSample(value=v, closed_ts=now_ts - i * spacing)
        for i, v in enumerate(values)
    ]


def test_gross_lcb_empty_series_insufficient():
    result = compute_gross_lcb([], now_ts=100_000, characteristic_time_seconds=_H_1H)
    assert result.verdict_band == "INSUFFICIENT"
    assert result.lcb is None
    assert result.n_eff == 0.0


def test_gross_lcb_below_n_eff_floor_no_verdict():
    """Fewer than 12 (effective) samples -> no verdict, never a demotion
    trigger (R2 item 2 cold-start non-starvation)."""
    samples = _mk([5.0] * 5, now_ts=100_000, spacing=10)
    result = compute_gross_lcb(samples, now_ts=100_000, characteristic_time_seconds=_H_1H)
    assert result.verdict_band == "INSUFFICIENT"
    assert result.lcb is None
    assert result.mean is None


def test_gross_lcb_cold_start_band_between_12_and_20():
    samples = _mk([5.0] * 15, now_ts=100_000, spacing=1)  # near-zero age -> n_eff ~ 15
    result = compute_gross_lcb(samples, now_ts=100_000, characteristic_time_seconds=_H_1H)
    assert result.verdict_band == "COLD_START"
    assert result.lcb is not None


def test_gross_lcb_full_band_at_or_above_20():
    samples = _mk([5.0] * 25, now_ts=100_000, spacing=1)
    result = compute_gross_lcb(samples, now_ts=100_000, characteristic_time_seconds=_H_1H)
    assert result.verdict_band == "FULL"


def test_gross_lcb_n_eff_capped_at_30():
    """A huge, evenly-weighted sample must not push N_eff (and therefore
    confidence/df) past the cap (R2 item 3)."""
    samples = _mk([5.0] * 500, now_ts=100_000, spacing=1)
    result = compute_gross_lcb(samples, now_ts=100_000, characteristic_time_seconds=_H_1H)
    assert result.n_eff == pytest.approx(N_EFF_CAP)


def test_gross_lcb_constant_series_lcb_equals_mean():
    """Zero-variance series -> SE=0 -> LCB collapses to the mean exactly."""
    samples = _mk([7.0] * 20, now_ts=100_000, spacing=1)
    result = compute_gross_lcb(samples, now_ts=100_000, characteristic_time_seconds=_H_1H)
    assert result.mean == pytest.approx(7.0)
    assert result.lcb == pytest.approx(7.0, abs=1e-9)


def test_gross_lcb_is_at_or_below_mean():
    """LCB is a LOWER confidence bound — must never exceed the mean for any
    series with positive dispersion."""
    values = [1.0, 5.0, -2.0, 3.0, 8.0, -1.0, 4.0, 2.0, 6.0, -3.0, 0.0, 5.0, 1.0, 2.0, 3.0]
    samples = _mk(values, now_ts=100_000, spacing=1)
    result = compute_gross_lcb(samples, now_ts=100_000, characteristic_time_seconds=_H_1H)
    assert result.mean is not None and result.lcb is not None
    assert result.lcb <= result.mean


def test_gross_lcb_stale_samples_downweighted_toward_recent():
    """A block of very old bad values plus a block of recent good values ->
    the weighted mean should sit closer to the RECENT value (time-decay
    doing its job, not a flat unweighted average)."""
    old = [
        GrossEdgeSample(value=-100.0, closed_ts=100_000 - 10 * _H_1H - i)
        for i in range(20)
    ]
    recent = [GrossEdgeSample(value=10.0, closed_ts=100_000 - i) for i in range(20)]
    result = compute_gross_lcb(
        old + recent, now_ts=100_000, characteristic_time_seconds=_H_1H
    )
    unweighted_mean = sum(s.value for s in old + recent) / 40
    assert result.mean is not None
    assert result.mean > unweighted_mean  # closer to +10 than the flat -45 avg


def test_gross_lcb_more_samples_tightens_gap_within_full_confidence_band():
    """Within the FULL band (n_eff>=20, confidence flat at 80%) more samples
    of the SAME distribution shrink the standard error -> tighter (or equal)
    LCB gap from the mean. (Cannot compare across the COLD_START->FULL
    boundary — the confidence ramp itself widens the required bound there
    by design, R2 item 2.)"""
    values = [1.0, 3.0, -1.0, 2.0, 4.0, 0.0, 2.0, 1.0, 3.0, -2.0, 1.0, 2.0]
    values_25 = (values * 3)[:25]
    values_75 = values * 7  # 84 samples, n_eff capped at 30
    small = compute_gross_lcb(
        _mk(values_25, spacing=0), now_ts=100_000, characteristic_time_seconds=_H_1H
    )
    large = compute_gross_lcb(
        _mk(values_75, spacing=0), now_ts=100_000, characteristic_time_seconds=_H_1H
    )
    assert small.verdict_band == "FULL" and large.verdict_band == "FULL"
    assert small.mean is not None and small.lcb is not None
    assert large.mean is not None and large.lcb is not None
    gap_small = small.mean - small.lcb
    gap_large = large.mean - large.lcb
    assert gap_large <= gap_small + 1e-9
