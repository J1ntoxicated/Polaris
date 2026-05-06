"""Tests for src/signals/composer.py — CompositeScorer (Phase 27).

TDD: tests written before implementation (P4 mandate).
P7: property-based tests via Hypothesis.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.signals.composer import (
    FactorInputs,
    CompositeScorer,
    composite_score,
)


# ---------------------------------------------------------------------------
# Unit tests — deterministic
# ---------------------------------------------------------------------------

class TestFactorInputsValidation:
    def test_valid_inputs_accepted(self):
        fi = FactorInputs(strength=0.5, momentum=0.5, volume=0.5, regime_fit=0.5, microstructure=0.5)
        assert fi.strength == 0.5

    def test_all_zeros_valid(self):
        fi = FactorInputs(strength=0.0, momentum=0.0, volume=0.0, regime_fit=0.0, microstructure=0.0)
        assert fi.strength == 0.0

    def test_all_ones_valid(self):
        fi = FactorInputs(strength=1.0, momentum=1.0, volume=1.0, regime_fit=1.0, microstructure=1.0)
        assert fi.strength == 1.0

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            FactorInputs(strength=1.1, momentum=0.5, volume=0.5, regime_fit=0.5, microstructure=0.5)

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            FactorInputs(strength=-0.1, momentum=0.5, volume=0.5, regime_fit=0.5, microstructure=0.5)

    def test_none_factor_allowed_as_zero(self):
        # None means factor not available — treated as 0.0 with 0 weight contribution
        fi = FactorInputs(strength=0.8, momentum=None, volume=0.6, regime_fit=0.4, microstructure=0.5)
        assert fi.momentum is None


class TestCompositeScorerWeights:
    def test_default_weights_sum_to_one(self):
        scorer = CompositeScorer()
        total = sum(scorer.weights.values())
        assert abs(total - 1.0) < 1e-9

    def test_custom_weights_accepted(self):
        w = {"strength": 0.4, "momentum": 0.2, "volume": 0.2, "regime_fit": 0.1, "microstructure": 0.1}
        scorer = CompositeScorer(weights=w)
        assert abs(sum(scorer.weights.values()) - 1.0) < 1e-9

    def test_custom_weights_not_summing_to_one_raises(self):
        w = {"strength": 0.5, "momentum": 0.5, "volume": 0.5, "regime_fit": 0.5, "microstructure": 0.5}
        with pytest.raises(ValueError):
            CompositeScorer(weights=w)

    def test_missing_factor_key_raises(self):
        w = {"strength": 0.5, "momentum": 0.3, "volume": 0.2}  # missing regime_fit, microstructure
        with pytest.raises(ValueError):
            CompositeScorer(weights=w)


class TestCompositeScorerScore:
    def test_all_max_factors_give_score_one(self):
        fi = FactorInputs(strength=1.0, momentum=1.0, volume=1.0, regime_fit=1.0, microstructure=1.0)
        scorer = CompositeScorer()
        result = scorer.score(fi)
        assert abs(result - 1.0) < 1e-9

    def test_all_zero_factors_give_score_zero(self):
        fi = FactorInputs(strength=0.0, momentum=0.0, volume=0.0, regime_fit=0.0, microstructure=0.0)
        scorer = CompositeScorer()
        result = scorer.score(fi)
        assert result == 0.0

    def test_score_in_range(self):
        fi = FactorInputs(strength=0.7, momentum=0.3, volume=0.5, regime_fit=0.8, microstructure=0.2)
        scorer = CompositeScorer()
        result = scorer.score(fi)
        assert 0.0 <= result <= 1.0

    def test_none_factor_excluded_from_weighted_average(self):
        # momentum=None → only 4 factors contribute; weights renormalized
        fi_none = FactorInputs(strength=0.5, momentum=None, volume=0.5, regime_fit=0.5, microstructure=0.5)
        fi_half = FactorInputs(strength=0.5, momentum=0.5, volume=0.5, regime_fit=0.5, microstructure=0.5)
        scorer = CompositeScorer()
        # With all=0.5 and equal weights, score should be 0.5 regardless of None
        assert abs(scorer.score(fi_none) - 0.5) < 1e-9
        assert abs(scorer.score(fi_half) - 0.5) < 1e-9

    def test_higher_strength_gives_higher_score(self):
        fi_low = FactorInputs(strength=0.2, momentum=0.5, volume=0.5, regime_fit=0.5, microstructure=0.5)
        fi_high = FactorInputs(strength=0.9, momentum=0.5, volume=0.5, regime_fit=0.5, microstructure=0.5)
        scorer = CompositeScorer()
        assert scorer.score(fi_high) > scorer.score(fi_low)

    def test_functional_alias_matches_scorer(self):
        fi = FactorInputs(strength=0.6, momentum=0.4, volume=0.7, regime_fit=0.3, microstructure=0.5)
        scorer = CompositeScorer()
        assert composite_score(fi) == scorer.score(fi)

    def test_custom_weights_affect_score(self):
        fi = FactorInputs(strength=1.0, momentum=0.0, volume=0.0, regime_fit=0.0, microstructure=0.0)
        # Weight strength heavily → score should be near 1.0
        w_heavy = {"strength": 0.9, "momentum": 0.025, "volume": 0.025, "regime_fit": 0.025, "microstructure": 0.025}
        w_light = {"strength": 0.1, "momentum": 0.225, "volume": 0.225, "regime_fit": 0.225, "microstructure": 0.225}
        scorer_heavy = CompositeScorer(weights=w_heavy)
        scorer_light = CompositeScorer(weights=w_light)
        assert scorer_heavy.score(fi) > scorer_light.score(fi)

    def test_all_none_factors_raises(self):
        fi = FactorInputs(strength=None, momentum=None, volume=None, regime_fit=None, microstructure=None)
        scorer = CompositeScorer()
        with pytest.raises(ValueError):
            scorer.score(fi)


# ---------------------------------------------------------------------------
# Property-based tests (P7 mandate)
# ---------------------------------------------------------------------------

factor_value = st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
positive_factor = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


@given(
    strength=positive_factor,
    momentum=positive_factor,
    volume=positive_factor,
    regime_fit=positive_factor,
    microstructure=positive_factor,
)
@settings(max_examples=200)
def test_property_score_always_in_unit_interval(strength, momentum, volume, regime_fit, microstructure):
    """Score must always be in [0, 1] for valid inputs."""
    fi = FactorInputs(strength=strength, momentum=momentum, volume=volume,
                      regime_fit=regime_fit, microstructure=microstructure)
    scorer = CompositeScorer()
    result = scorer.score(fi)
    assert 0.0 <= result <= 1.0, f"Score {result} out of [0,1]"


@given(
    strength=positive_factor,
    momentum=positive_factor,
    volume=positive_factor,
    regime_fit=positive_factor,
    microstructure=positive_factor,
)
@settings(max_examples=100)
def test_property_score_monotone_in_strength(strength, momentum, volume, regime_fit, microstructure):
    """Increasing strength alone must not decrease composite score."""
    assume(strength < 1.0)
    fi_low = FactorInputs(strength=strength, momentum=momentum, volume=volume,
                          regime_fit=regime_fit, microstructure=microstructure)
    fi_high = FactorInputs(strength=min(strength + 0.1, 1.0), momentum=momentum, volume=volume,
                           regime_fit=regime_fit, microstructure=microstructure)
    scorer = CompositeScorer()
    assert scorer.score(fi_high) >= scorer.score(fi_low) - 1e-12


@given(
    s=positive_factor,
    m=positive_factor,
    v=positive_factor,
    r=positive_factor,
    ms=positive_factor,
)
@settings(max_examples=100)
def test_property_score_deterministic(s, m, v, r, ms):
    """Same inputs always produce identical score (pure function)."""
    fi = FactorInputs(strength=s, momentum=m, volume=v, regime_fit=r, microstructure=ms)
    scorer = CompositeScorer()
    assert scorer.score(fi) == scorer.score(fi)


@given(
    strength=factor_value,
    momentum=factor_value,
    volume=factor_value,
    regime_fit=factor_value,
    microstructure=factor_value,
)
@settings(max_examples=200)
def test_property_none_handling_never_raises(strength, momentum, volume, regime_fit, microstructure):
    """None factors must not raise unless ALL factors are None."""
    all_none = all(x is None for x in [strength, momentum, volume, regime_fit, microstructure])
    fi = FactorInputs(strength=strength, momentum=momentum, volume=volume,
                      regime_fit=regime_fit, microstructure=microstructure)
    scorer = CompositeScorer()
    if all_none:
        with pytest.raises(ValueError):
            scorer.score(fi)
    else:
        result = scorer.score(fi)
        assert 0.0 <= result <= 1.0
