"""Unit tests for composer v2 (raw_score + SignalContract.merge).

Covers:
  * raw_score aggregation (NO confidence multiplier — north-star
    feedback_no_defensive_param_dampen)
  * SignalContract.merge edge_prob / execution_risk / direction_certainty
  * CompositeSignal.contract default = SignalContract.neutral()

Legacy confidence-dampen path was deleted in MSG-P0-5 (2026-04-18); the
flag `signal_contract_enabled_v2` default is now 1 and composer always
uses v2. Flag-off parity tests were removed with the legacy branch.

Scope: isolated — builds SignalResult fixtures by hand, composes via the
real CompositeScorer.compose() entry point; no providers / market_data /
engine pipeline involved.
"""
from __future__ import annotations

import pytest

from invasion.config._registry_api import REGISTRY
from invasion.signals.base import CompositeSignal, SignalResult
from invasion.signals.composer import CompositeScorer
from invasion.signals.contract import SignalContract


# ── helpers ──────────────────────────────────────────────────────────


def _make_scorer(weights: dict[str, float]) -> CompositeScorer:
    """Build a scorer with explicit weights; no provider registration."""
    s = CompositeScorer(weights=weights)
    # compose() reads weight_map from self._providers; populate with dummies
    # bound to the same weight so weight_map is consistent with `weights`.
    class _Dummy:
        def __init__(self, name):
            self.name = name

    s._providers = [(_Dummy(n), w) for n, w in weights.items()]
    return s


def _set_flag(name: str, value):
    """Mutate a preg directly via REGISTRY for test isolation."""
    param = REGISTRY[name]
    original = param.current
    param.current = value
    return original


# ── v2: raw_score + contract merge ──────────────────────────────────


def test_flag_on_raw_score_aggregation():
    """Flag=1: weighted_sum must NOT multiply by confidence.

    Same 2 signals: score=30 w=10, score=40 w=20 (confidence ignored).
    Expected v2 composite:
        weighted_sum = 30*10 + 40*20 = 300 + 800 = 1100
        total_weight = 10 + 20 = 30
        composite    = 1100 / 30 ≈ 36.6667
    """
    original = _set_flag("signal_contract_enabled_v2", 1)
    try:
        scorer = _make_scorer({"a": 10.0, "b": 20.0})
        sigs = [
            SignalResult(name="a", score=30, confidence=0.7, ttl=900),
            SignalResult(name="b", score=40, confidence=0.8, ttl=900),
        ]
        comp = scorer.compose("TEST", sigs, regime="neutral")

        assert comp.score == pytest.approx(1100.0 / 30.0, abs=1e-6)
        # v2 score differs from legacy (36.95...) — confidence dampen gone.
        assert comp.score != pytest.approx(850.0 / 23.0, abs=1e-3)
    finally:
        _set_flag("signal_contract_enabled_v2", original)


def test_flag_on_contract_merge_edge_prob():
    """Flag=1: composite.edge_prob = weighted mean of provider contracts."""
    original = _set_flag("signal_contract_enabled_v2", 1)
    try:
        scorer = _make_scorer({"a": 10.0, "b": 10.0})
        sigs = [
            SignalResult(
                name="a", score=30, confidence=0.7, ttl=900,
                contract=SignalContract(edge_prob=0.6, direction_certainty=0.4),
            ),
            SignalResult(
                name="b", score=40, confidence=0.8, ttl=900,
                contract=SignalContract(edge_prob=0.75, direction_certainty=0.6),
            ),
        ]
        comp = scorer.compose("TEST", sigs, regime="neutral")

        # weights equal → simple mean
        assert comp.edge_prob == pytest.approx(0.675, abs=1e-6)
        # contract field populated (not neutral)
        assert comp.contract.edge_prob == pytest.approx(0.675, abs=1e-6)
        # confidence field maps to direction_certainty (geo mean of 0.4, 0.6)
        import math
        assert comp.confidence == pytest.approx(math.sqrt(0.24), abs=1e-6)
    finally:
        _set_flag("signal_contract_enabled_v2", original)


def test_flag_on_contract_merge_execution_risk_max():
    """Flag=1: contract.execution_risk_bps = max across providers (conservative)."""
    original = _set_flag("signal_contract_enabled_v2", 1)
    try:
        scorer = _make_scorer({"a": 10.0, "b": 10.0})
        sigs = [
            SignalResult(
                name="a", score=30, confidence=0.7, ttl=900,
                contract=SignalContract(execution_risk_bps=5.0),
            ),
            SignalResult(
                name="b", score=40, confidence=0.8, ttl=900,
                contract=SignalContract(execution_risk_bps=15.0),
            ),
        ]
        comp = scorer.compose("TEST", sigs, regime="neutral")
        assert comp.contract.execution_risk_bps == 15.0
        # legacy execution_risk field = bps / 10000 (normalised [0,1])
        assert comp.execution_risk == pytest.approx(15.0 / 10000.0, abs=1e-9)
    finally:
        _set_flag("signal_contract_enabled_v2", original)


def test_flag_on_no_active_signals_neutral_contract():
    """Flag=1 + all-expired signals → neutral contract, zero score."""
    original = _set_flag("signal_contract_enabled_v2", 1)
    try:
        scorer = _make_scorer({"a": 10.0})
        # confidence=0 → dropped by guard
        sigs = [SignalResult(name="a", score=30, confidence=0.0, ttl=900)]
        comp = scorer.compose("TEST", sigs, regime="neutral")
        assert comp.score == 0.0
        assert comp.contract == SignalContract.neutral()
    finally:
        _set_flag("signal_contract_enabled_v2", original)


# ── CompositeSignal contract field back-compat ──────────────────────


def test_composite_signal_contract_default_neutral():
    """CompositeSignal(...) without `contract` kwarg → neutral fallback.

    Guards the 3 engine.py rebuild sites: if any caller forgets to pass
    `contract=...`, they still get a valid SignalContract instance.
    """
    c = CompositeSignal(
        ticker="TEST",
        score=40.0,
        direction="long",
        confidence=0.7,
    )
    assert isinstance(c.contract, SignalContract)
    assert c.contract == SignalContract.neutral()


def test_composite_signal_contract_roundtrip():
    custom = SignalContract(edge_prob=0.65, execution_risk_bps=9.0)
    c = CompositeSignal(
        ticker="TEST",
        score=40.0,
        direction="long",
        confidence=0.7,
        contract=custom,
    )
    assert c.contract is custom
    assert c.contract.edge_prob == 0.65
