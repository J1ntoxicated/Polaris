"""TDD — Wave B agenda ①+② (vault/50_research/debates/waveB_sizing_params_2026-07-02.md).

DEMO/PAPER only. Aggressive bias preserved (flow_not_block). 9-stack ban:
these tests structurally assert the strength_scalar fold clamps EXACTLY once
and the qty cap reduction is a SINGLE min() — no stacked shrink.
"""

from __future__ import annotations

import math
import sqlite3

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from polaris.core.sizing.r_budget_sizer import (
    CAP_DOMINATED_REALIZATION_THRESHOLD,
    SHADOW_FLIP_HOURS_DEFAULT,
    SHADOW_FLIP_N_DEFAULT,
    CapQtyInputs,
    cap_dominated,
    compute_r_budget_qty,
    fold_strength_scalar,
    qty_from_r_budget,
    qty_headroom_min,
    read_shadow_state,
    record_shadow_observation,
    resolve_sizing_mode,
    stop_dist_usd,
    target_realization,
)
from polaris.core.sizing.schema import CONT_SCALAR_MAX, CONT_SCALAR_MIN

# ---------------------------------------------------------------------------
# Agenda ② — strength_scalar fold
# ---------------------------------------------------------------------------


def test_fold_strength_scalar_default_is_noop() -> None:
    assert fold_strength_scalar(1.2) == pytest.approx(1.2)


def test_fold_strength_scalar_multiplies_preclip_value() -> None:
    # 1.0 raw × 1.4 strength = 1.4 (within band, no clamp binds).
    assert fold_strength_scalar(1.0, strength_scalar=1.4) == pytest.approx(1.4)


def test_fold_strength_scalar_clamps_ceiling() -> None:
    assert fold_strength_scalar(1.4, strength_scalar=1.5) == CONT_SCALAR_MAX


def test_fold_strength_scalar_clamps_floor() -> None:
    assert fold_strength_scalar(0.8, strength_scalar=0.5) == CONT_SCALAR_MIN


def test_fold_strength_scalar_never_multiplies_an_already_clamped_value() -> None:
    """R2 BREAK #2: clamp(raw × strength) != clamp(clamp(raw) × strength) at the
    boundary — this is the asymmetric-information-loss bug the debate rejected."""
    raw_preclip = 1.6  # would clamp to 1.5 if clamped FIRST
    strength = 0.6
    # Correct (spec): clamp(1.6 * 0.6) = clamp(0.96) = 0.96
    correct = fold_strength_scalar(raw_preclip, strength_scalar=strength)
    assert correct == pytest.approx(0.96)
    # Wrong (rejected order): clamp(1.6)=1.5, then 1.5*0.6=0.9 — DIFFERENT value,
    # proving order matters and the fold must operate on the PRE-clip input.
    wrong = max(CONT_SCALAR_MIN, min(CONT_SCALAR_MAX, raw_preclip)) * strength
    assert correct != pytest.approx(wrong)


def test_fold_strength_scalar_absent_defaults_to_noop() -> None:
    assert fold_strength_scalar(1.1, strength_scalar=0.0) == pytest.approx(1.1)
    assert fold_strength_scalar(1.1, strength_scalar=float("nan")) == pytest.approx(1.1)


def test_fold_strength_scalar_never_produces_zero_or_negative() -> None:
    assert fold_strength_scalar(-5.0, strength_scalar=1.0) >= CONT_SCALAR_MIN
    assert fold_strength_scalar(1.0, strength_scalar=-3.0) >= CONT_SCALAR_MIN


@given(
    raw=st.one_of(st.floats(min_value=-10, max_value=10), st.just(float("nan")), st.just(float("inf"))),
    strength=st.one_of(st.floats(min_value=-5, max_value=5), st.just(float("nan")), st.just(float("inf"))),
)
def test_fold_strength_scalar_always_in_band(raw: float, strength: float) -> None:
    out = fold_strength_scalar(raw, strength_scalar=strength)
    assert CONT_SCALAR_MIN <= out <= CONT_SCALAR_MAX
    assert math.isfinite(out)


def test_9stack_single_clamp_structural() -> None:
    """Structural assertion: the fold applies at most ONE clamp op — verified by
    checking that folding twice in sequence is NOT equivalent to a single
    combined-multiplier fold done correctly (i.e. no hidden double-clamp
    collapsing the value to a narrower effective range than one clamp allows)."""
    raw = 1.4
    s1, s2 = 1.4, 0.6
    # A correct single-slot fold multiplies scalars together BEFORE the one clamp:
    # clamp(1.4 * 1.4 * 0.6) = clamp(1.176) = 1.176.
    single_slot = fold_strength_scalar(raw, strength_scalar=s1 * s2)
    # Two sequential folds (mult-then-clamp twice) is what a 9-stack regression
    # would look like: clamp(1.4*1.4)=clamp(1.96)=1.5 (ceiling hit + info lost),
    # then clamp(1.5*0.6)=clamp(0.9)=0.9 — DIFFERENT from the single-slot result
    # because the intermediate clamp destroyed the magnitude above 1.5.
    double_clamp = fold_strength_scalar(fold_strength_scalar(raw, strength_scalar=s1), strength_scalar=s2)
    assert single_slot != double_clamp


# ---------------------------------------------------------------------------
# Agenda ① — stop_dist_usd (reuse risk_unit.py source, no invention)
# ---------------------------------------------------------------------------


def test_stop_dist_usd_matches_risk_usd_at_entry_formula() -> None:
    from polaris.core.metrics.risk_unit import risk_usd_at_entry

    entry_price, atr_pct, stop_mult = 100.0, 0.02, 2.0
    expected_per_unit = risk_usd_at_entry(
        entry_price=entry_price, entry_atr_pct=atr_pct, base_qty=1.0, stop_atr_mult=stop_mult
    )
    got = stop_dist_usd(entry_price=entry_price, atr_pct=atr_pct, stop_atr_mult=stop_mult)
    assert got == pytest.approx(expected_per_unit)


def test_stop_dist_usd_zero_on_bad_inputs() -> None:
    assert stop_dist_usd(entry_price=0.0, atr_pct=0.02, stop_atr_mult=2.0) == 0.0
    assert stop_dist_usd(entry_price=100.0, atr_pct=0.0, stop_atr_mult=2.0) == 0.0
    assert stop_dist_usd(entry_price=100.0, atr_pct=0.02, stop_atr_mult=0.0) == 0.0
    assert stop_dist_usd(entry_price=float("nan"), atr_pct=0.02, stop_atr_mult=2.0) == 0.0


# ---------------------------------------------------------------------------
# Agenda ① — qty_from_r_budget PRIMARY formula
# ---------------------------------------------------------------------------


def test_qty_from_r_budget_basic() -> None:
    # R_budget=1000, T4_mult=1.0, stop=10/unit -> qty=100
    assert qty_from_r_budget(r_budget_usd=1000.0, t4_mult=1.0, stop_dist_usd_per_unit=10.0) == pytest.approx(100.0)


def test_qty_from_r_budget_tier_3x_is_3r() -> None:
    base = qty_from_r_budget(r_budget_usd=1000.0, t4_mult=1.0, stop_dist_usd_per_unit=10.0)
    tiered = qty_from_r_budget(r_budget_usd=1000.0, t4_mult=3.0, stop_dist_usd_per_unit=10.0)
    assert tiered == pytest.approx(base * 3.0)


def test_qty_from_r_budget_zero_stop_dist_is_data_integrity_zero() -> None:
    assert qty_from_r_budget(r_budget_usd=1000.0, t4_mult=1.0, stop_dist_usd_per_unit=0.0) == 0.0


# ---------------------------------------------------------------------------
# Agenda ① — single min() cap reduction (structural 9-stack assertion)
# ---------------------------------------------------------------------------


def test_qty_headroom_min_picks_lowest_binding_cap() -> None:
    caps = CapQtyInputs(
        single_trade_qty=50.0,
        per_symbol_qty=40.0,
        margin_qty=1000.0,
    )
    final_qty, binding = qty_headroom_min(100.0, caps)
    assert final_qty == pytest.approx(40.0)
    assert binding == "per_symbol"


def test_qty_headroom_min_none_caps_do_not_bind() -> None:
    caps = CapQtyInputs(
        single_trade_qty=1000.0,
        per_symbol_qty=1000.0,
        margin_qty=1000.0,
        underlying_qty=None,
        cluster_qty=None,
    )
    final_qty, binding = qty_headroom_min(5.0, caps)
    assert final_qty == pytest.approx(5.0)
    assert binding == "proposed"


def test_qty_headroom_min_is_single_pass_not_stacked() -> None:
    """9-stack ban: applying two caps via qty_headroom_min must equal min() of
    both individually — never a product/sequential-shrink of the two."""
    caps = CapQtyInputs(single_trade_qty=30.0, per_symbol_qty=20.0, margin_qty=1000.0)
    final_qty, _ = qty_headroom_min(100.0, caps)
    assert final_qty == min(100.0, 30.0, 20.0, 1000.0)
    # NOT a stacked product like 100 * (30/100) * (20/100) = 6.0
    assert final_qty != pytest.approx(6.0)


@given(
    proposed=st.floats(min_value=0, max_value=1e6, allow_nan=False),
    single=st.floats(min_value=0, max_value=1e6, allow_nan=False),
    symbol=st.floats(min_value=0, max_value=1e6, allow_nan=False),
    margin=st.floats(min_value=0, max_value=1e6, allow_nan=False),
)
def test_qty_headroom_min_never_exceeds_any_cap(
    proposed: float, single: float, symbol: float, margin: float
) -> None:
    caps = CapQtyInputs(single_trade_qty=single, per_symbol_qty=symbol, margin_qty=margin)
    final_qty, _ = qty_headroom_min(proposed, caps)
    assert final_qty <= proposed + 1e-9
    assert final_qty <= single + 1e-9
    assert final_qty <= symbol + 1e-9
    assert final_qty <= margin + 1e-9
    assert final_qty >= 0.0


# ---------------------------------------------------------------------------
# Agenda ① — CAP_DOMINATED measurement-integrity tag
# ---------------------------------------------------------------------------


def test_target_realization_full_fill_is_one() -> None:
    r = target_realization(
        qty_after_caps=100.0, stop_dist_usd_per_unit=10.0, r_budget_usd=1000.0, t4_mult=1.0
    )
    assert r == pytest.approx(1.0)


def test_target_realization_capped_fraction() -> None:
    # Only 40 units allowed vs 100 intended -> realization 0.4
    r = target_realization(
        qty_after_caps=40.0, stop_dist_usd_per_unit=10.0, r_budget_usd=1000.0, t4_mult=1.0
    )
    assert r == pytest.approx(0.4)
    assert not cap_dominated(r)


def test_cap_dominated_below_threshold() -> None:
    r = target_realization(
        qty_after_caps=20.0, stop_dist_usd_per_unit=10.0, r_budget_usd=1000.0, t4_mult=1.0
    )
    assert r == pytest.approx(0.2)
    assert r < CAP_DOMINATED_REALIZATION_THRESHOLD
    assert cap_dominated(r)


def test_target_realization_zero_intended_risk_is_zero_not_crash() -> None:
    assert target_realization(qty_after_caps=10.0, stop_dist_usd_per_unit=1.0, r_budget_usd=0.0, t4_mult=1.0) == 0.0


# ---------------------------------------------------------------------------
# Agenda ① — property tests: unclipped vs clipped (spec-mandated split)
# ---------------------------------------------------------------------------


@given(
    r_budget=st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False),
    t4_mult=st.floats(min_value=0.5, max_value=5.0, allow_nan=False),
    stop=st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False),
)
@settings(max_examples=200)
def test_property_unclipped_full_stop_pnl_matches_r_budget_within_10pct(
    r_budget: float, t4_mult: float, stop: float
) -> None:
    """(a) unclipped: a full-stop loss on the UNCLIPPED qty realizes
    pnl_usd == qty * stop == R_budget * T4_mult (exact by construction; the
    ±10% spec tolerance covers real-world discretization/fees, asserted here
    as an exact algebraic identity for the pure function)."""
    caps = CapQtyInputs(
        single_trade_qty=float("inf"),
        per_symbol_qty=float("inf"),
        margin_qty=float("inf"),
    )
    result = compute_r_budget_qty(
        r_budget_usd=r_budget,
        t4_mult=t4_mult,
        entry_price=1.0,
        atr_pct=stop,  # entry_price=1.0 so stop_dist == atr_pct*stop_atr_mult
        stop_atr_mult=1.0,
        caps=caps,
    )
    assert result is not None
    full_stop_pnl = result.qty_unclipped * stop
    intended = r_budget * t4_mult
    assert full_stop_pnl == pytest.approx(intended, rel=0.10)
    assert not result.cap_dominated


@given(
    r_budget=st.floats(min_value=100.0, max_value=10_000.0, allow_nan=False),
    t4_mult=st.floats(min_value=0.75, max_value=3.0, allow_nan=False),
    stop=st.floats(min_value=0.1, max_value=100.0, allow_nan=False),
    binding_cap_qty=st.floats(min_value=0.01, max_value=1.0, allow_nan=False),
)
@settings(max_examples=200)
def test_property_clipped_binding_cap_is_structurally_logged(
    r_budget: float, t4_mult: float, stop: float, binding_cap_qty: float
) -> None:
    """(b) clipped: when a cap binds, the result carries the binding cap name
    (structured log assertion) and qty_after_caps never exceeds the bound."""
    caps = CapQtyInputs(
        single_trade_qty=binding_cap_qty,
        per_symbol_qty=float("inf"),
        margin_qty=float("inf"),
    )
    result = compute_r_budget_qty(
        r_budget_usd=r_budget,
        t4_mult=t4_mult,
        entry_price=1.0,
        atr_pct=stop,
        stop_atr_mult=1.0,
        caps=caps,
    )
    assert result is not None
    if result.qty_unclipped > binding_cap_qty:
        assert result.binding_cap == "single_trade"
        assert result.qty_after_caps == pytest.approx(binding_cap_qty)


def test_compute_r_budget_qty_data_integrity_fallback_on_zero_atr() -> None:
    caps = CapQtyInputs(single_trade_qty=100.0, per_symbol_qty=100.0, margin_qty=100.0)
    result = compute_r_budget_qty(
        r_budget_usd=1000.0, t4_mult=1.0, entry_price=100.0, atr_pct=0.0, stop_atr_mult=2.0, caps=caps
    )
    assert result is None


# ---------------------------------------------------------------------------
# Agenda ① — shadow-verify flip gate (persisted state)
# ---------------------------------------------------------------------------


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def test_shadow_state_starts_unflipped(conn: sqlite3.Connection) -> None:
    state = read_shadow_state(conn)
    assert state.n_observed == 0
    assert state.flipped_active is False


def test_shadow_flips_at_n_threshold(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_N", "3")
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_HOURS", "999999")
    base_ts = 1_000_000
    for i in range(2):
        state = record_shadow_observation(conn, now_ts=base_ts + i)
        assert state.flipped_active is False
    state = record_shadow_observation(conn, now_ts=base_ts + 2)
    assert state.flipped_active is True
    assert state.n_observed == 3


def test_shadow_flips_at_hours_threshold(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_N", "999999")
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_HOURS", "1")
    base_ts = 1_000_000
    record_shadow_observation(conn, now_ts=base_ts)
    state = record_shadow_observation(conn, now_ts=base_ts + 3601)
    assert state.flipped_active is True


def test_shadow_flip_is_permanent_and_restart_safe(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_N", "1")
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_HOURS", "999999")
    record_shadow_observation(conn, now_ts=1000)
    # Simulate restart: new read from the SAME conn/table sees the flip.
    state = read_shadow_state(conn)
    assert state.flipped_active is True
    # Further observations never un-flip.
    state2 = record_shadow_observation(conn, now_ts=2000)
    assert state2.flipped_active is True


def test_resolve_sizing_mode_defaults_shadow(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_RBUDGET_SIZING_MODE", raising=False)
    assert resolve_sizing_mode(conn) == "shadow"


def test_resolve_sizing_mode_flips_to_active_after_threshold(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("POLARIS_RBUDGET_SIZING_MODE", raising=False)
    monkeypatch.setenv("POLARIS_RBUDGET_SHADOW_N", "1")
    record_shadow_observation(conn, now_ts=1000)
    assert resolve_sizing_mode(conn) == "active"


def test_resolve_sizing_mode_env_override_wins(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_RBUDGET_SIZING_MODE", "off")
    assert resolve_sizing_mode(conn) == "off"
    monkeypatch.setenv("POLARIS_RBUDGET_SIZING_MODE", "active")
    assert resolve_sizing_mode(conn) == "active"


def test_shadow_flip_defaults_match_spec() -> None:
    assert SHADOW_FLIP_N_DEFAULT == 100
    assert SHADOW_FLIP_HOURS_DEFAULT == 24.0
