"""Unit tests — CompositeScorer (F-N7 PR4).

Covers:
  * Group-aware provider filter (forex drops crypto-only providers)
  * Provider agreement amplifies composite (two providers agree)
  * Provider disagreement cancels (two providers oppose → ~0)
  * Expired signals dropped from composite
  * Low-confidence (conf<=0) signals dropped
  * Remap: sweet-spot boost
  * Remap: overheat damp
  * Invariant I-C1: composer must NOT multiply raw_score by confidence
    (flag=1 branch)

Scope: unit — no providers, no market_data/engine wiring. Builds
SignalResult fixtures by hand and drives CompositeScorer.compose()
directly (same pattern as test_composer_rewire.py).

Run: `pytest tests/signals/test_composer.py -v`
"""
from __future__ import annotations

import pytest

from invasion.config._registry_api import REGISTRY
from invasion.signals.base import SignalResult
from invasion.signals.composer import (
    CompositeScorer,
    _remap_contrarian_score,
)


# ── helpers ──────────────────────────────────────────────────────────


def _set_preg(name: str, value):
    """Mutate a preg directly via REGISTRY; return original for restore."""
    param = REGISTRY[name]
    original = param.current
    param.current = value
    return original


class _DummyProvider:
    """Minimal SignalProvider stub — carries a name for composer."""

    def __init__(self, name: str):
        self.name = name


def _make_scorer(weights: dict[str, float]) -> CompositeScorer:
    """Build a scorer with explicit weights + matching dummy providers."""
    s = CompositeScorer(weights=weights)
    s._providers = [(_DummyProvider(n), w) for n, w in weights.items()]
    return s


# ── 1. Group filter ─────────────────────────────────────────────────


def test_composer_group_filter_forex_drops_crypto_providers():
    """Forex group — crypto-only providers (funding, liquidation, ls_ratio,
    taker) must be filtered out of the applicable set.

    We inspect _GROUP_PROVIDERS directly since that's the authoritative
    filter consulted by score() before any provider is invoked.
    """
    forex_set = CompositeScorer._GROUP_PROVIDERS["forex"]
    crypto_only = {"funding", "liquidation", "ls_ratio", "taker"}
    # None of the crypto-only providers should be whitelisted for forex.
    leaked = crypto_only & forex_set
    assert leaked == set(), (
        f"crypto-only providers leaked into forex group: {leaked}"
    )
    # Sanity: technical IS allowed for forex.
    assert "technical" in forex_set


# ── 2. Agreement / disagreement ─────────────────────────────────────


def test_composer_agreement_amplifies_two_providers_agree():
    """Two long-agreeing providers → composite score clearly long.

    v2 path (raw_score, confidence as telemetry only — no dampen):
      weighted_sum = 40*10 + 50*10 = 400 + 500 = 900
      total_weight = 10 + 10       = 20
      composite    = 900 / 20      = 45.0 (strong long)
    """
    scorer = _make_scorer({"a": 10.0, "b": 10.0})
    sigs = [
        SignalResult(name="a", score=40, confidence=0.8, ttl=900),
        SignalResult(name="b", score=50, confidence=0.9, ttl=900),
    ]
    comp = scorer.compose("TEST", sigs, regime="neutral")
    assert comp.score == pytest.approx(45.0, abs=1e-2)
    assert comp.direction == "long"
    assert comp.score > 30.0  # amplified agreement


def test_composer_disagreement_cancels():
    """Two providers with opposite directions, equal weight/confidence →
    composite ~0 (cancellation)."""
    orig = _set_preg("signal_contract_enabled_v2", 0)
    try:
        scorer = _make_scorer({"a": 10.0, "b": 10.0})
        sigs = [
            SignalResult(name="a", score=40, confidence=0.8, ttl=900),
            SignalResult(name="b", score=-40, confidence=0.8, ttl=900),
        ]
        comp = scorer.compose("TEST", sigs, regime="neutral")
        # Symmetric → sums cancel exactly.
        assert comp.score == pytest.approx(0.0, abs=1e-6)
        assert comp.direction == "neutral"
    finally:
        _set_preg("signal_contract_enabled_v2", orig)


# ── 3. Drop rules ───────────────────────────────────────────────────


def test_composer_expired_signals_dropped():
    """Expired signal must not contribute to composite.

    Construct a signal with ttl=1 and timestamp=0 (epoch) so is_expired
    is True by a huge margin. compose() must exclude it — only the
    fresh signal drives the composite.
    """
    orig = _set_preg("signal_contract_enabled_v2", 0)
    try:
        scorer = _make_scorer({"fresh": 10.0, "stale": 10.0})
        fresh = SignalResult(name="fresh", score=40, confidence=0.8, ttl=900)
        stale = SignalResult(
            name="stale", score=-40, confidence=0.8, ttl=1, timestamp=1.0,
        )
        assert stale.is_expired
        comp = scorer.compose("TEST", [fresh, stale], regime="neutral")
        # Only fresh survives → composite matches single-signal path
        # (40 * 10 * 0.8) / (10 * 0.8) = 40 (minus tiny decay on fresh).
        assert comp.score == pytest.approx(fresh.decayed_score, abs=1e-2)
        assert comp.direction == "long"
    finally:
        _set_preg("signal_contract_enabled_v2", orig)


def test_composer_low_conf_filter():
    """confidence<=0 signal must be filtered out (guard in compose)."""
    orig = _set_preg("signal_contract_enabled_v2", 0)
    try:
        scorer = _make_scorer({"good": 10.0, "zero": 10.0})
        good = SignalResult(name="good", score=40, confidence=0.8, ttl=900)
        zero = SignalResult(name="zero", score=-80, confidence=0.0, ttl=900)
        comp = scorer.compose("TEST", [good, zero], regime="neutral")
        # zero dropped → composite matches single-signal path (~40).
        assert comp.score == pytest.approx(40.0, abs=1.0)
        assert comp.direction == "long"
    finally:
        _set_preg("signal_contract_enabled_v2", orig)


# ── 4. Remap ────────────────────────────────────────────────────────


def test_remap_sweet_spot_boost():
    """Score in [sweet_lo, sweet_hi] → multiplied by sweet_boost, tag=sweet_spot.

    We pin preg values explicitly so the test is immune to live tuning
    drift (sweet_spot_boost has floated between 1.05 and 1.15).
    """
    orig_lo = _set_preg("sweet_spot_lo", 25)
    orig_hi = _set_preg("sweet_spot_hi", 45)
    orig_boost = _set_preg("sweet_spot_boost", 1.10)
    try:
        result, tag = _remap_contrarian_score(35.0)
        assert tag == "sweet_spot"
        assert result == pytest.approx(35.0 * 1.10, abs=1e-6)
        # Negative: sign preserved.
        neg_result, neg_tag = _remap_contrarian_score(-35.0)
        assert neg_tag == "sweet_spot"
        assert neg_result == pytest.approx(-35.0 * 1.10, abs=1e-6)
    finally:
        _set_preg("sweet_spot_lo", orig_lo)
        _set_preg("sweet_spot_hi", orig_hi)
        _set_preg("sweet_spot_boost", orig_boost)


def test_remap_overheat_damp():
    """|score| in [overheat_threshold, extreme_threshold) → damp, tag=overheat.

    Pin preg values explicitly to avoid drift sensitivity.
    """
    orig_thresh = _set_preg("overheat_threshold", 60)
    orig_damp = _set_preg("overheat_damp", 0.90)
    orig_ext = _set_preg("extreme_threshold", 80)
    try:
        result, tag = _remap_contrarian_score(65.0)
        assert tag == "overheat"
        assert result == pytest.approx(65.0 * 0.90, abs=1e-6)
    finally:
        _set_preg("overheat_threshold", orig_thresh)
        _set_preg("overheat_damp", orig_damp)
        _set_preg("extreme_threshold", orig_ext)


# ── 5. Invariant I-C1 ───────────────────────────────────────────────


@pytest.mark.invariant
def test_inv_C1_no_confidence_multiply():
    """Invariant I-C1: under flag=1 the composer MUST NOT multiply
    raw_score by confidence. Confidence is telemetry only; conviction
    lives on SignalContract.

    Evidence: same 2 signals with different confidence values must
    produce IDENTICAL composite scores under flag=1 (confidence
    ignored). Under flag=0 (legacy) the same inputs would diverge.
    """
    orig = _set_preg("signal_contract_enabled_v2", 1)
    try:
        scorer_a = _make_scorer({"a": 10.0, "b": 20.0})
        sigs_high_conf = [
            SignalResult(name="a", score=30, confidence=0.9, ttl=900),
            SignalResult(name="b", score=40, confidence=0.9, ttl=900),
        ]
        comp_high = scorer_a.compose("TEST", sigs_high_conf, regime="neutral")

        scorer_b = _make_scorer({"a": 10.0, "b": 20.0})
        sigs_low_conf = [
            SignalResult(name="a", score=30, confidence=0.1, ttl=900),
            SignalResult(name="b", score=40, confidence=0.1, ttl=900),
        ]
        comp_low = scorer_b.compose("TEST", sigs_low_conf, regime="neutral")

        # I-C1: confidence must NOT multiply raw_score → identical scores
        # (modulo sub-ms decay drift between the two compose() calls).
        assert comp_high.score == pytest.approx(comp_low.score, abs=1e-3)
        # And the score equals pure raw_score weighted mean:
        #   (30*10 + 40*20) / (10 + 20) = 1100/30 ≈ 36.6667
        assert comp_high.score == pytest.approx(1100.0 / 30.0, abs=1e-2)
    finally:
        _set_preg("signal_contract_enabled_v2", orig)
