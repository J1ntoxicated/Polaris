"""Tests for scale_gate (fee-split v0 — tier-amplifier-only withhold).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo). Spec:
vault/50_research/debates/fee_split_judgment_2026-07-10.md R2 items 2 + 4.
"""
from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from polaris.core.classes.scale_gate import (
    GROSS_EDGE_FEE_UNPROVEN_REASON,
    evaluate_scale_gate,
)


def test_edge_proven_above_bar_passes_amplifier_through():
    result = evaluate_scale_gate(
        gross_lcb=30.0, resolved_tier_amp=2.0, best_case_friction=20.0, edge_margin=5.0,
    )
    assert not result.binding
    assert result.applied_tier_amp == pytest.approx(2.0)
    assert result.reason is None


def test_edge_unproven_below_bar_withholds_to_1_0():
    result = evaluate_scale_gate(
        gross_lcb=22.0, resolved_tier_amp=3.0, best_case_friction=20.0, edge_margin=5.0,
    )
    assert result.binding
    assert result.applied_tier_amp == pytest.approx(1.0)
    assert result.reason == GROSS_EDGE_FEE_UNPROVEN_REASON


def test_exactly_at_bar_is_not_proven_strict_inequality():
    """gross_LCB == bar exactly does not clear the bar (spec: strictly '>')."""
    result = evaluate_scale_gate(
        gross_lcb=25.0, resolved_tier_amp=1.5, best_case_friction=20.0, edge_margin=5.0,
    )
    assert result.binding
    assert result.applied_tier_amp == pytest.approx(1.0)


def test_no_verdict_yet_none_lcb_withholds_conservatively():
    result = evaluate_scale_gate(
        gross_lcb=None, resolved_tier_amp=1.5, best_case_friction=20.0, edge_margin=5.0,
    )
    assert result.binding
    assert result.applied_tier_amp == pytest.approx(1.0)
    assert result.reason == GROSS_EDGE_FEE_UNPROVEN_REASON


def test_no_amplifier_earned_gate_is_a_no_op_even_with_bad_edge():
    """resolved_tier_amp already 1.0 (no bonus earned) -> the gate must not
    itself report a binding/withhold state — nothing to withhold."""
    result = evaluate_scale_gate(
        gross_lcb=None, resolved_tier_amp=1.0, best_case_friction=20.0, edge_margin=5.0,
    )
    assert not result.binding
    assert result.applied_tier_amp == pytest.approx(1.0)
    assert result.reason is None


def test_reason_is_gross_edge_fee_unproven_not_bad_strategy():
    """Explicit label check: the withhold reason must be the neutral
    GROSS_EDGE_FEE_UNPROVEN label, never anything BAD_STRATEGY-flavored."""
    result = evaluate_scale_gate(
        gross_lcb=0.0, resolved_tier_amp=2.0, best_case_friction=20.0, edge_margin=5.0,
    )
    assert result.reason == "GROSS_EDGE_FEE_UNPROVEN"
    assert "BAD" not in (result.reason or "")


# ---------------------------------------------------------------------------
# property: baseline invariance — the gate is a PURE tier_amp fallback, it
# never emits a value outside {resolved_tier_amp, 1.0} and never negative/
# non-finite (no_defensive_param_dampen — the ONLY degradation path is the
# 1.0 fallback, never a partial/scaled dampen).
# ---------------------------------------------------------------------------


@given(
    gross_lcb=st.one_of(st.none(), st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False)),
    resolved_tier_amp=st.sampled_from([1.0, 1.5, 2.0, 3.0]),
    best_case_friction=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
    edge_margin=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
)
def test_property_applied_tier_amp_is_always_resolved_or_one(
    gross_lcb: float | None,
    resolved_tier_amp: float,
    best_case_friction: float,
    edge_margin: float,
) -> None:
    result = evaluate_scale_gate(
        gross_lcb=gross_lcb, resolved_tier_amp=resolved_tier_amp,
        best_case_friction=best_case_friction, edge_margin=edge_margin,
    )
    assert result.applied_tier_amp in (resolved_tier_amp, 1.0)
    assert result.applied_tier_amp <= resolved_tier_amp
    assert result.binding == (result.applied_tier_amp != resolved_tier_amp)
    if result.binding:
        assert result.reason == GROSS_EDGE_FEE_UNPROVEN_REASON
    else:
        assert result.reason is None
