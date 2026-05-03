"""Unit tests for T2-2 PR1 SignalContract (dormant scaffold).

Covers:
  * neutral defaults
  * merge(): empty / single / weighted / edge cases
  * is_tradeable gate
  * Invariant I-C1: frozen dataclass (no dampening channel)
  * SignalResult.contract default factory (kwarg-less construction)
"""
from __future__ import annotations

import math

import pytest

from invasion.signals.base import SignalResult
from invasion.signals.contract import SignalContract


# ── neutral defaults ─────────────────────────────────────────────────

def test_neutral_defaults():
    c = SignalContract()
    assert c.edge_prob == 0.5
    assert c.reversal_horizon_sec == 900
    assert c.execution_risk_bps == 5.0
    assert c.direction_certainty == 0.0


def test_neutral_classmethod():
    assert SignalContract.neutral() == SignalContract()


# ── merge ────────────────────────────────────────────────────────────

def test_merge_empty_returns_neutral():
    assert SignalContract.merge([]) == SignalContract.neutral()


def test_merge_single_returns_same():
    c = SignalContract(edge_prob=0.62, reversal_horizon_sec=600,
                       execution_risk_bps=8.0, direction_certainty=0.4)
    assert SignalContract.merge([c]) == c


def test_merge_edge_prob_weighted_mean():
    a = SignalContract(edge_prob=0.6)
    b = SignalContract(edge_prob=0.8)
    m = SignalContract.merge([a, b], weights=[1.0, 1.0])
    assert m.edge_prob == pytest.approx(0.7)


def test_merge_edge_prob_weighted_unequal():
    a = SignalContract(edge_prob=0.4)
    b = SignalContract(edge_prob=0.8)
    m = SignalContract.merge([a, b], weights=[3.0, 1.0])
    # (0.4*3 + 0.8*1) / 4 = 0.5
    assert m.edge_prob == pytest.approx(0.5)


def test_merge_reversal_median():
    horizons = [600, 900, 1800]
    contracts = [SignalContract(reversal_horizon_sec=h) for h in horizons]
    m = SignalContract.merge(contracts)
    # median_high semantics: sorted[len//2] = sorted[1] = 900
    assert m.reversal_horizon_sec == 900


def test_merge_execution_risk_max():
    contracts = [
        SignalContract(execution_risk_bps=5.0),
        SignalContract(execution_risk_bps=12.0),
        SignalContract(execution_risk_bps=20.0),
    ]
    m = SignalContract.merge(contracts)
    assert m.execution_risk_bps == 20.0


def test_merge_direction_certainty_geometric():
    contracts = [
        SignalContract(direction_certainty=0.5),
        SignalContract(direction_certainty=0.8),
    ]
    m = SignalContract.merge(contracts)
    # sqrt(0.5 * 0.8) = sqrt(0.4) ≈ 0.6324555
    assert m.direction_certainty == pytest.approx(math.sqrt(0.4))


def test_merge_direction_certainty_all_zero():
    contracts = [SignalContract(), SignalContract()]
    m = SignalContract.merge(contracts)
    assert m.direction_certainty == 0.0


def test_merge_direction_certainty_skips_zero():
    # 0.0 exclude → geometric mean over only 0.6 → 0.6
    contracts = [
        SignalContract(direction_certainty=0.0),
        SignalContract(direction_certainty=0.6),
    ]
    m = SignalContract.merge(contracts)
    assert m.direction_certainty == pytest.approx(0.6)


def test_merge_weight_count_mismatch_raises():
    with pytest.raises(ValueError, match="contract/weight mismatch"):
        SignalContract.merge(
            [SignalContract(), SignalContract()],
            weights=[1.0],
        )


def test_merge_edge_prob_clamped():
    # Construct contracts whose weighted mean sits inside [0,1]; the clamp
    # guard is a defensive safety net. Verify a boundary case works.
    contracts = [SignalContract(edge_prob=1.0), SignalContract(edge_prob=1.0)]
    m = SignalContract.merge(contracts)
    assert m.edge_prob == 1.0


# ── is_tradeable ─────────────────────────────────────────────────────

def test_is_tradeable_pass():
    c = SignalContract(edge_prob=0.60, execution_risk_bps=8.0)
    assert c.is_tradeable(edge_floor=0.52, risk_cap_bps=12.0) is True


def test_is_tradeable_edge_below_floor():
    c = SignalContract(edge_prob=0.50, execution_risk_bps=8.0)
    assert c.is_tradeable(edge_floor=0.52, risk_cap_bps=12.0) is False


def test_is_tradeable_risk_above_cap():
    c = SignalContract(edge_prob=0.60, execution_risk_bps=15.0)
    assert c.is_tradeable(edge_floor=0.52, risk_cap_bps=12.0) is False


def test_is_tradeable_boundary_inclusive():
    # equality at floor + cap should PASS (>=, <=)
    c = SignalContract(edge_prob=0.52, execution_risk_bps=12.0)
    assert c.is_tradeable(edge_floor=0.52, risk_cap_bps=12.0) is True


# ── Invariant I-C1: frozen + no confidence ───────────────────────────

def test_frozen_dataclass_blocks_mutation():
    from dataclasses import FrozenInstanceError
    c = SignalContract()
    with pytest.raises(FrozenInstanceError):
        c.edge_prob = 0.9  # type: ignore[misc]


def test_invariant_no_confidence_field():
    """I-C1: SignalContract MUST NOT carry a confidence/dampener field.

    Compile-time guarantee that the composer cannot multiply raw_score by
    a contract-channel confidence scalar.
    """
    fields = {f for f in SignalContract.__dataclass_fields__}
    assert "confidence" not in fields
    assert "dampen" not in fields
    assert "dampener" not in fields


# ── SignalResult back-compat ─────────────────────────────────────────

def test_signal_result_default_contract():
    r = SignalResult(name="test", score=30)
    assert isinstance(r.contract, SignalContract)
    assert r.contract == SignalContract.neutral()


def test_signal_result_kwargs_preserved():
    # Legacy call shape (confidence kwarg) still works.
    r = SignalResult(name="legacy", score=20, confidence=0.8, ttl=600)
    assert r.confidence == 0.8
    assert r.ttl == 600
    assert r.contract == SignalContract.neutral()


def test_signal_result_explicit_contract():
    custom = SignalContract(edge_prob=0.65, execution_risk_bps=9.0)
    r = SignalResult(name="c", score=40, contract=custom)
    assert r.contract is custom
    assert r.contract.edge_prob == 0.65
