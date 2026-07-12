"""Capital Rotation core evaluator — pure-logic unit tests (TDD-first).

DEMO/PAPER ONLY — virtual funds. These tests pin the *correctness* contract of
the rotation evaluator, which is a CAPITAL-EFFICIENCY redeploy, NOT a defensive
throttle:

  - both sides are compared in the SAME unit (expected DOLLAR edge),
  - the improvement margin is ADDITIVE ($), never multiplicative — the weak side
    is negative-by-selection so a multiplicative (1+x) margin would invert,
  - the forward score is the posterior LOWER CREDIBLE BOUND (never the raw point
    estimate — winner's/loser's-curse guard) and only at n>=POSTERIOR_MIN_N,
  - a COLD-START candidate (n<N) must NEVER out-rank a measured-negative held
    position (otherwise rotation would churn into unproven cells),
  - winners (exit_state in {protected, harvest}) are NEVER rotated out,
  - the round-trip rotation COST ($) is subtracted from the improvement,
  - rotation is PER-VENUE only (OKX USDT vs Capital margin are non-fungible).

The evaluator adds NO T4 sizing multiplier and performs NO I/O; every DB/venue
value is injected. AGGRESSIVE bias preserved (no P&L halt, no block filter).
"""

from __future__ import annotations

import math

import pytest

from polaris.core.rotation import (
    POSTERIOR_MIN_N,
    ROTATION_DEFAULT_IMPROVEMENT_MARGIN_USD,
    CellStats,
    HeldPosition,
    RotationCandidate,
    choose_rotation,
    expected_dollar_edge_held,
    expected_dollar_edge_new,
    forward_score,
    rotation_cost_usd,
    select_weakest,
    should_rotate,
)

# --------------------------------------------------------------------------
# Helpers — build posterior-bearing cell stats at a chosen sample count.
# --------------------------------------------------------------------------


def _cell_with_posterior(*, mu: float, sd: float, n: int) -> CellStats:
    """A cell whose NIG marginal-t on mu has the given (mu, scale, df via n).

    ``posterior_sd`` is the Student-t scale; ``df = 2*alpha_n`` is supplied via
    ``alpha_n`` (alpha_n = 1 + n/2, mirroring posterior.nig_update).
    """
    alpha_n = 1.0 + n / 2.0
    return CellStats(mu=mu, posterior_sd=sd, alpha_n=alpha_n, n_samples=n, avg_pnl_r=mu)


def _cold_cell(*, avg_pnl_r: float, n: int) -> CellStats:
    """A cold cell: no usable posterior (sd None), only an avg_pnl_r fallback."""
    return CellStats(
        mu=None, posterior_sd=None, alpha_n=None, n_samples=n, avg_pnl_r=avg_pnl_r
    )


# --------------------------------------------------------------------------
# 1. forward_score — lower credible bound at n>=N, cold-start conservative.
# --------------------------------------------------------------------------


def test_forward_score_is_lower_credible_bound_not_point_estimate() -> None:
    # mu=0.50, but with real dispersion the lower bound is strictly below mu.
    cell = _cell_with_posterior(mu=0.50, sd=0.40, n=40)
    score = forward_score(cell, lower_credible_bound=0.90)
    assert score is not None
    assert score < 0.50  # never the raw point estimate (curse guard)


def test_forward_score_tighter_dispersion_scores_higher() -> None:
    wide = forward_score(_cell_with_posterior(mu=0.50, sd=0.80, n=40), lower_credible_bound=0.90)
    tight = forward_score(_cell_with_posterior(mu=0.50, sd=0.20, n=40), lower_credible_bound=0.90)
    assert wide is not None and tight is not None
    assert tight > wide  # less uncertainty => higher (closer to mu) lower bound


def test_forward_score_cold_start_candidate_ineligible() -> None:
    # n < POSTERIOR_MIN_N and no posterior => not eligible to rank as a candidate.
    cold = _cold_cell(avg_pnl_r=0.20, n=POSTERIOR_MIN_N - 1)
    assert forward_score(cold, lower_credible_bound=0.90) is None


def test_forward_score_at_threshold_n_is_eligible() -> None:
    cell = _cell_with_posterior(mu=0.30, sd=0.30, n=POSTERIOR_MIN_N)
    assert forward_score(cell, lower_credible_bound=0.90) is not None


def test_cold_candidate_does_not_outrank_measured_negative_held() -> None:
    # The core failure the cross-verify warns about: a cold candidate must not
    # beat a measured-negative held position. A cold candidate is ineligible
    # (None), so it can never produce a finite E$_new to win the comparison.
    cold_candidate = _cold_cell(avg_pnl_r=0.50, n=5)
    assert forward_score(cold_candidate, lower_credible_bound=0.90) is None


# --------------------------------------------------------------------------
# 2-3. expected dollar edge — both sides same unit ($).
# --------------------------------------------------------------------------


def test_expected_dollar_edge_new_uses_risk_pct_as_capital_scale_only() -> None:
    # E$ = fwd_mu(edge, R) * proposed_risk_pct(capital scale) * equity.
    val = expected_dollar_edge_new(fwd_mu_new=0.40, proposed_risk_pct=0.05, equity=10_000.0)
    assert val == pytest.approx(0.40 * 0.05 * 10_000.0)  # 200.0


def test_expected_dollar_edge_held_same_formula_shape() -> None:
    val = expected_dollar_edge_held(fwd_R_held=-0.30, open_risk_pct_held=0.06, equity=10_000.0)
    assert val == pytest.approx(-0.30 * 0.06 * 10_000.0)  # -180.0


def test_both_sides_are_dollars_and_comparable() -> None:
    e_new = expected_dollar_edge_new(fwd_mu_new=0.40, proposed_risk_pct=0.05, equity=10_000.0)
    e_held = expected_dollar_edge_held(fwd_R_held=-0.30, open_risk_pct_held=0.06, equity=10_000.0)
    # Same unit => a finite numeric difference is meaningful.
    assert math.isfinite(e_new - e_held)
    assert e_new - e_held == pytest.approx(200.0 - (-180.0))


# --------------------------------------------------------------------------
# 4. rotation_cost_usd — round-trip slip+fee in $ (mirrors cost_adjusted_pnl_r).
# --------------------------------------------------------------------------


def test_rotation_cost_capital_uses_per_leg_bps_both_legs() -> None:
    # Capital demo: COST_BPS_CAPITAL per leg on close(A) + open(B).
    cost = rotation_cost_usd(
        close_size_usd=1_000.0,
        open_size_usd=2_000.0,
        venue="capital",
    )
    # 2 legs * (3 bps/1e4) per side: close round-trip-leg + open round-trip-leg.
    # close leg = 3bps*1000, open leg = 3bps*2000 (one taker leg each side).
    assert cost > 0.0
    assert cost == pytest.approx((3.0 / 1e4) * 1_000.0 + (3.0 / 1e4) * 2_000.0)


def test_rotation_cost_okx_uses_fee_and_slippage() -> None:
    cost = rotation_cost_usd(
        close_size_usd=1_000.0,
        open_size_usd=1_000.0,
        venue="okx",
        close_fee_usd=0.5,
        open_fee_usd=0.5,
        close_slippage_bps=2.0,
        open_slippage_bps=2.0,
    )
    expected = 0.5 + 0.5 + (2.0 / 1e4) * 1_000.0 + (2.0 / 1e4) * 1_000.0
    assert cost == pytest.approx(expected)


def test_rotation_cost_is_non_negative() -> None:
    assert rotation_cost_usd(close_size_usd=0.0, open_size_usd=0.0, venue="okx") >= 0.0


def test_rotation_cost_real_zero_slippage_not_overridden() -> None:
    """Ledger-reconcile forensic 2026-07-12, bug① symmetric fix: a real 0.0
    slippage passed by the caller must NOT get the 1bp fallback baked in —
    only the ``None`` default (no fill data supplied) does."""
    cost = rotation_cost_usd(
        close_size_usd=1_000.0,
        open_size_usd=1_000.0,
        venue="okx",
        close_fee_usd=0.5,
        open_fee_usd=0.5,
        close_slippage_bps=0.0,
        open_slippage_bps=0.0,
    )
    assert cost == pytest.approx(0.5 + 0.5)  # fee-only — no fallback slippage


# --------------------------------------------------------------------------
# 5. should_rotate — ADDITIVE margin, subtract cost.
# --------------------------------------------------------------------------


def test_should_rotate_additive_margin_fires_on_negative_weak_score() -> None:
    # E$_new = +200, E$_held = -180 (negative-by-selection).
    # diff = 380. margin=50 + cost=20 => 70. 380 > 70 => rotate.
    assert should_rotate(e_new=200.0, e_held=-180.0, margin_usd=50.0, cost_usd=20.0) is True


def test_should_rotate_blocks_when_improvement_below_margin_plus_cost() -> None:
    # diff = 60, margin+cost = 70 => do NOT rotate.
    assert should_rotate(e_new=100.0, e_held=40.0, margin_usd=50.0, cost_usd=20.0) is False


def test_should_rotate_strict_inequality_at_boundary() -> None:
    # diff exactly == margin+cost => NOT a strict win => no rotate.
    assert should_rotate(e_new=100.0, e_held=30.0, margin_usd=50.0, cost_usd=20.0) is False


def test_additive_margin_not_multiplicative_when_held_negative() -> None:
    # A multiplicative (1+m) margin on a negative held would LOWER the bar
    # (e_held*(1+m) is MORE negative), inverting intent. Additive keeps the bar
    # at e_held + margin + cost. Pin that additive semantics:
    e_new, e_held, margin, cost = 10.0, -100.0, 50.0, 5.0
    # additive bar: e_held + margin + cost = -45 => e_new(10) > -45 => rotate.
    assert should_rotate(e_new=e_new, e_held=e_held, margin_usd=margin, cost_usd=cost) is True
    # but a small improvement that does not clear margin+cost over held must fail
    assert should_rotate(e_new=-60.0, e_held=-100.0, margin_usd=50.0, cost_usd=5.0) is False


# --------------------------------------------------------------------------
# 6. select_weakest — lowest E$_held among eligible, winners exempt.
# --------------------------------------------------------------------------


def _held(
    *,
    pid: str,
    fwd_R: float | None,
    risk_pct: float,
    exit_state: str = "open",
    pnl_r: float = -0.2,
    held_sec: float = 600.0,
) -> HeldPosition:
    return HeldPosition(
        position_id=pid,
        venue="okx",
        fwd_R_held=fwd_R,
        open_risk_pct_held=risk_pct,
        exit_state=exit_state,
        pnl_r=pnl_r,
        held_sec=held_sec,
    )


def test_select_weakest_picks_lowest_dollar_edge_held() -> None:
    a = _held(pid="A", fwd_R=-0.10, risk_pct=0.05)  # E$ = -0.10*0.05*E
    b = _held(pid="B", fwd_R=-0.40, risk_pct=0.05)  # most negative => weakest
    victim = select_weakest([a, b], equity=10_000.0)
    assert victim is not None and victim.position_id == "B"


def test_select_weakest_excludes_winners_protected_and_harvest() -> None:
    loser = _held(pid="L", fwd_R=-0.50, risk_pct=0.06, exit_state="protected", pnl_r=-0.5)
    only = _held(pid="O", fwd_R=-0.10, risk_pct=0.05)
    victim = select_weakest([loser, only], equity=10_000.0)
    assert victim is not None and victim.position_id == "O"  # protected exempt
    harvest = _held(pid="H", fwd_R=-0.90, risk_pct=0.06, exit_state="harvest", pnl_r=-0.9)
    victim2 = select_weakest([harvest, only], equity=10_000.0)
    assert victim2 is not None and victim2.position_id == "O"


def test_select_weakest_requires_negative_pnl_and_min_hold() -> None:
    winning = _held(pid="W", fwd_R=-0.30, risk_pct=0.06, pnl_r=+0.1)  # pnl>=0 ineligible
    too_fresh = _held(pid="F", fwd_R=-0.40, risk_pct=0.06, pnl_r=-0.4, held_sec=10.0)
    eligible = _held(pid="E", fwd_R=-0.05, risk_pct=0.05, pnl_r=-0.05, held_sec=600.0)
    victim = select_weakest([winning, too_fresh, eligible], equity=10_000.0)
    assert victim is not None and victim.position_id == "E"


def test_select_weakest_none_when_no_eligible() -> None:
    winning = _held(pid="W", fwd_R=-0.30, risk_pct=0.06, pnl_r=+0.1)
    protected = _held(pid="P", fwd_R=-0.30, risk_pct=0.06, exit_state="protected", pnl_r=-0.3)
    assert select_weakest([winning, protected], equity=10_000.0) is None


def test_select_weakest_cold_start_held_uses_avg_pnl_fallback() -> None:
    # A held with fwd_R from avg_pnl_r fallback (no posterior) is still rankable.
    cold = _held(pid="C", fwd_R=-0.20, risk_pct=0.06, pnl_r=-0.2)
    measured = _held(pid="M", fwd_R=-0.05, risk_pct=0.05, pnl_r=-0.05)
    victim = select_weakest([cold, measured], equity=10_000.0)
    assert victim is not None and victim.position_id == "C"  # most negative E$


# --------------------------------------------------------------------------
# 7. choose_rotation — per-venue, greedy single-weakest vs single-best.
# --------------------------------------------------------------------------


def _candidate(
    *,
    cid: str,
    venue: str,
    cell: CellStats,
    risk_pct: float,
    size_usd: float = 1_000.0,
) -> RotationCandidate:
    return RotationCandidate(
        candidate_id=cid,
        venue=venue,
        cell_stats=cell,
        proposed_risk_pct=risk_pct,
        proposed_size_usd=size_usd,
    )


def test_choose_rotation_fires_when_new_beats_weak_by_margin_plus_cost() -> None:
    strong_cell = _cell_with_posterior(mu=0.60, sd=0.20, n=40)
    cand = _candidate(cid="c1", venue="okx", cell=strong_cell, risk_pct=0.06)
    weak = _held(pid="w1", fwd_R=-0.50, risk_pct=0.06, pnl_r=-0.5)
    result = choose_rotation(
        blocked_candidates=[cand],
        held_positions=[weak],
        equity=10_000.0,
        venue="okx",
        improvement_margin_usd=10.0,
    )
    assert result is not None
    victim, winner = result
    assert victim.position_id == "w1"
    assert winner.candidate_id == "c1"


def test_choose_rotation_per_venue_filter_isolates_venues() -> None:
    strong_cell = _cell_with_posterior(mu=0.60, sd=0.20, n=40)
    okx_cand = _candidate(cid="okx_c", venue="okx", cell=strong_cell, risk_pct=0.06)
    cap_weak = HeldPosition(
        position_id="cap_w",
        venue="capital",
        fwd_R_held=-0.50,
        open_risk_pct_held=0.06,
        exit_state="open",
        pnl_r=-0.5,
        held_sec=600.0,
    )
    # OKX candidate cannot rotate a Capital held (non-fungible margin).
    result = choose_rotation(
        blocked_candidates=[okx_cand],
        held_positions=[cap_weak],
        equity=10_000.0,
        venue="okx",
        improvement_margin_usd=10.0,
    )
    assert result is None


def test_choose_rotation_cold_candidate_cannot_beat_measured_negative_held() -> None:
    cold = _cold_cell(avg_pnl_r=0.80, n=5)  # looks great but unproven => ineligible
    cand = _candidate(cid="cold", venue="okx", cell=cold, risk_pct=0.06)
    weak = _held(pid="w", fwd_R=-0.30, risk_pct=0.06, pnl_r=-0.3)
    result = choose_rotation(
        blocked_candidates=[cand],
        held_positions=[weak],
        equity=10_000.0,
        venue="okx",
        improvement_margin_usd=10.0,
    )
    assert result is None  # cold candidate ineligible => never rotates


def test_choose_rotation_no_eligible_victim_returns_none() -> None:
    strong_cell = _cell_with_posterior(mu=0.60, sd=0.20, n=40)
    cand = _candidate(cid="c1", venue="okx", cell=strong_cell, risk_pct=0.06)
    winner_held = _held(pid="p", fwd_R=0.40, risk_pct=0.06, exit_state="harvest", pnl_r=0.4)
    result = choose_rotation(
        blocked_candidates=[cand],
        held_positions=[winner_held],
        equity=10_000.0,
        venue="okx",
        improvement_margin_usd=10.0,
    )
    assert result is None


def test_choose_rotation_does_not_fire_when_cost_eats_improvement() -> None:
    # Slightly-better candidate; large rotation cost should block the swap.
    small_edge = _cell_with_posterior(mu=0.06, sd=0.05, n=40)
    cand = _candidate(cid="c1", venue="okx", cell=small_edge, risk_pct=0.05, size_usd=1_000.0)
    weak = _held(pid="w", fwd_R=-0.02, risk_pct=0.05, pnl_r=-0.02)
    result = choose_rotation(
        blocked_candidates=[cand],
        held_positions=[weak],
        equity=10_000.0,
        venue="okx",
        improvement_margin_usd=5.0,
        rotation_cost_usd_value=10_000.0,  # absurd cost => never worth it
    )
    assert result is None


def test_choose_rotation_picks_best_candidate_against_weakest_held() -> None:
    weakish = _candidate(cid="weakish", venue="okx", cell=_cell_with_posterior(mu=0.20, sd=0.10, n=40), risk_pct=0.05)
    strongest = _candidate(cid="strongest", venue="okx", cell=_cell_with_posterior(mu=0.70, sd=0.10, n=40), risk_pct=0.06)
    weak = _held(pid="w1", fwd_R=-0.40, risk_pct=0.06, pnl_r=-0.4)
    other = _held(pid="w2", fwd_R=-0.10, risk_pct=0.05, pnl_r=-0.1)
    result = choose_rotation(
        blocked_candidates=[weakish, strongest],
        held_positions=[weak, other],
        equity=10_000.0,
        venue="okx",
        improvement_margin_usd=10.0,
    )
    assert result is not None
    victim, winner = result
    assert victim.position_id == "w1"  # weakest held
    assert winner.candidate_id == "strongest"  # best candidate


# --------------------------------------------------------------------------
# 7b. choose_rotation reason_out — observability ONLY (no behavior change).
# A capital-blocked candidate that does not fire must surface WHY so a silent
# stall is visible in the bot log. reason_out is a pure out-param: the
# return value and fire/no-fire decision are unchanged whether it is passed.
# --------------------------------------------------------------------------


def test_reason_out_no_eligible_victim_all_winners() -> None:
    # All held are winners (harvest) => no eligible victim, even with a strong
    # candidate. The no-fire reason must be surfaced.
    strong_cell = _cell_with_posterior(mu=0.60, sd=0.20, n=40)
    cand = _candidate(cid="c1", venue="okx", cell=strong_cell, risk_pct=0.06)
    winner_held = _held(pid="p", fwd_R=0.40, risk_pct=0.06, exit_state="harvest", pnl_r=0.4)
    reason: dict[str, object] = {}
    result = choose_rotation(
        blocked_candidates=[cand],
        held_positions=[winner_held],
        equity=10_000.0,
        venue="okx",
        improvement_margin_usd=10.0,
        reason_out=reason,
    )
    assert result is None  # behavior unchanged
    assert reason["reason"] == "no_eligible_victim"


def test_reason_out_all_candidates_cold() -> None:
    # Candidate cell is cold (n<N) => ineligible; a measured-negative held exists
    # (eligible victim) => the no-fire reason is the cold candidate, not the victim.
    cold = _cold_cell(avg_pnl_r=0.80, n=5)
    cand = _candidate(cid="cold", venue="okx", cell=cold, risk_pct=0.06)
    weak = _held(pid="w", fwd_R=-0.30, risk_pct=0.06, pnl_r=-0.3)
    reason: dict[str, object] = {}
    result = choose_rotation(
        blocked_candidates=[cand],
        held_positions=[weak],
        equity=10_000.0,
        venue="okx",
        improvement_margin_usd=10.0,
        reason_out=reason,
    )
    assert result is None  # behavior unchanged
    assert reason["reason"] == "all_candidates_cold"
    assert reason["weakest_victim"] == "w"  # victim was found before the cold check


def test_reason_out_below_margin() -> None:
    # Slightly-better candidate but an absurd injected cost eats the improvement
    # => below_margin. The diagnostic edges/margin/cost must be surfaced.
    small_edge = _cell_with_posterior(mu=0.06, sd=0.05, n=40)
    cand = _candidate(cid="c1", venue="okx", cell=small_edge, risk_pct=0.05, size_usd=1_000.0)
    weak = _held(pid="w", fwd_R=-0.02, risk_pct=0.05, pnl_r=-0.02)
    reason: dict[str, object] = {}
    result = choose_rotation(
        blocked_candidates=[cand],
        held_positions=[weak],
        equity=10_000.0,
        venue="okx",
        improvement_margin_usd=5.0,
        rotation_cost_usd_value=10_000.0,  # absurd cost => never worth it
        reason_out=reason,
    )
    assert result is None  # behavior unchanged
    assert reason["reason"] == "below_margin"
    assert reason["weakest_victim"] == "w"
    assert reason["cost"] == pytest.approx(10_000.0)
    assert "e_held" in reason and "e_new" in reason and "margin" in reason


def test_reason_out_omitted_leaves_behavior_identical() -> None:
    # Without reason_out the function behaves EXACTLY as before (return value and
    # decision unchanged) — pins that the out-param is pure observability.
    strong_cell = _cell_with_posterior(mu=0.60, sd=0.20, n=40)
    cand = _candidate(cid="c1", venue="okx", cell=strong_cell, risk_pct=0.06)
    weak = _held(pid="w1", fwd_R=-0.50, risk_pct=0.06, pnl_r=-0.5)
    with_reason: dict[str, object] = {}
    fired_with = choose_rotation(
        blocked_candidates=[cand], held_positions=[weak], equity=10_000.0,
        venue="okx", improvement_margin_usd=10.0, reason_out=with_reason,
    )
    fired_without = choose_rotation(
        blocked_candidates=[cand], held_positions=[weak], equity=10_000.0,
        venue="okx", improvement_margin_usd=10.0,
    )
    # A fire returns the same (victim, winner); reason_out stays empty on a fire.
    assert fired_with is not None and fired_without is not None
    assert fired_with[0].position_id == fired_without[0].position_id
    assert fired_with[1].candidate_id == fired_without[1].candidate_id
    assert with_reason == {}  # no reason written when it fires


# --------------------------------------------------------------------------
# Param surface sanity — defaults exist and are env-tunable constants.
# --------------------------------------------------------------------------


def test_param_defaults_exposed() -> None:
    assert POSTERIOR_MIN_N == 20
    assert ROTATION_DEFAULT_IMPROVEMENT_MARGIN_USD > 0.0
