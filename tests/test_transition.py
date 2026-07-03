"""Tests for the pts-classes transition state machine (group C).

DEMO/PAPER only (virtual capital, OKX SPOT demo + Capital CFD demo).
``transition.py`` is a pure capital-ROUTING state machine — never a
block/reject filter. A strategy demoted to PROVE/BENCH/KILL keeps
signaling/learning/shadow-pricing regardless (aggressive_always_profit /
no_block_filter_architecture). No sizing/9-stack/-1.0R rail touch — this
module only decides which class/ladder_step a (venue, strategy_id) track
occupies; capital allocation elsewhere reads the result.
"""
from __future__ import annotations

import pytest

from polaris.core.classes.transition import (
    TransitionInput,
    evaluate_transition,
)

BASE_TS = 1_700_000_000
DAY = 86_400


def _inp(**kw: object) -> TransitionInput:
    defaults: dict[str, object] = dict(
        strategy_class="EARN",
        kill_state="ACTIVE",
        window_w=20,
        dwell=0,
        ladder_step=0,
        epoch_id=1,
        last_transition_ts=BASE_TS,
        last_earn_demotion_ts=None,
        last_promotion_ts=None,
        timeframe_bucket="intraday",
        shadow_scores=[],
        intent_scores=[],
        recent_r_multiples=[],
        n_fills_total=100,
        n_signals_recent=100,
        n_fills_recent=100,
        last_50_fill_rate=1.0,
        now_ts=BASE_TS + 3600,
    )
    defaults.update(kw)
    return TransitionInput(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# KILL priority — no auto-revival
# ---------------------------------------------------------------------------


def test_kill_state_never_auto_transitions() -> None:
    inp = _inp(
        strategy_class="BENCH",
        kill_state="KILLED",
        shadow_scores=[10.0] * 60,  # would otherwise blow through every ladder step
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.kill_state == "KILLED"
    assert result.reason == "KILL_NO_AUTO_REVIVE"


def test_kill_checked_before_tripwire() -> None:
    inp = _inp(
        strategy_class="EARN",
        kill_state="KILLED",
        recent_r_multiples=[-1.0] * 8,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"
    assert result.reason == "KILL_NO_AUTO_REVIVE"


# ---------------------------------------------------------------------------
# Schmitt asymmetric hysteresis (EARN demotion)
# ---------------------------------------------------------------------------


def test_earn_stable_when_scores_healthy() -> None:
    inp = _inp(strategy_class="EARN", shadow_scores=[1.0] * 20, intent_scores=[1.0] * 20)
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"


def test_earn_to_prove_on_partial_window_mild_negative() -> None:
    # ceil(0.4*20) = 8 most-recent closes averaging < -1
    scores = [1.0] * 12 + [-2.0] * 8
    inp = _inp(strategy_class="EARN", window_w=20, intent_scores=scores)
    result = evaluate_transition(inp)
    assert result.strategy_class == "PROVE"
    assert result.reason == "SCHMITT_EARN_TO_PROVE"


def test_earn_to_bench_on_partial_window_severe_negative() -> None:
    # ceil(0.4*20) = 8 most-recent closes averaging < -4
    scores = [1.0] * 12 + [-5.0] * 8
    inp = _inp(strategy_class="EARN", window_w=20, intent_scores=scores)
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.reason == "SCHMITT_EARN_TO_BENCH"


def test_earn_to_bench_on_full_window_moderate_negative() -> None:
    # full W=20 closes averaging < -3 (but partial-8 average doesn't clear -4)
    scores = [-3.5] * 20
    inp = _inp(strategy_class="EARN", window_w=20, intent_scores=scores)
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.reason == "SCHMITT_EARN_TO_BENCH"


def test_earn_bench_severity_wins_over_prove_when_both_trigger() -> None:
    # partial-8 average clears BOTH -1 (prove) and -4 (bench) thresholds
    scores = [1.0] * 12 + [-10.0] * 8
    inp = _inp(strategy_class="EARN", window_w=20, intent_scores=scores)
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"


def test_schmitt_only_applies_to_earn_class() -> None:
    scores = [-10.0] * 20
    inp = _inp(strategy_class="PROVE", window_w=20, shadow_scores=scores, intent_scores=scores)
    result = evaluate_transition(inp)
    # PROVE demotion is governed by the stagnation rule, not Schmitt EARN rules
    assert result.reason != "SCHMITT_EARN_TO_BENCH"
    assert result.reason != "SCHMITT_EARN_TO_PROVE"


# ---------------------------------------------------------------------------
# dwell — PROVE->EARN 10 closes, post-EARN-demotion cooldown 5 closes
# ---------------------------------------------------------------------------


def test_prove_to_earn_blocked_before_dwell_10() -> None:
    inp = _inp(
        strategy_class="PROVE",
        dwell=9,
        window_w=20,
        intent_scores=[5.0] * 20,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "PROVE"
    assert result.reason == "DWELL_LOCKED"


def test_prove_to_earn_allowed_at_dwell_10() -> None:
    inp = _inp(
        strategy_class="PROVE",
        dwell=10,
        window_w=20,
        intent_scores=[5.0] * 20,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"
    assert result.reason == "PROVE_TO_EARN"


def test_prove_to_earn_blocked_by_post_demotion_cooldown() -> None:
    # dwell satisfied, but strategy was demoted from EARN only 4 closes worth
    # of wall-clock ago (< 5 dwell) — cooldown still active even though the
    # PROVE-side dwell counter alone would allow promotion.
    inp = _inp(
        strategy_class="PROVE",
        dwell=10,
        window_w=20,
        intent_scores=[5.0] * 20,
        last_earn_demotion_ts=BASE_TS,
        now_ts=BASE_TS + 3600,
        dwell_since_earn_demotion=4,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "PROVE"
    assert result.reason == "DWELL_LOCKED"


def test_prove_to_earn_allowed_after_post_demotion_cooldown_clears() -> None:
    inp = _inp(
        strategy_class="PROVE",
        dwell=10,
        window_w=20,
        intent_scores=[5.0] * 20,
        last_earn_demotion_ts=BASE_TS,
        dwell_since_earn_demotion=5,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"


# ---------------------------------------------------------------------------
# tripwire — bypasses dwell/cap, immediate
# ---------------------------------------------------------------------------


def test_tripwire_intraday_last8() -> None:
    inp = _inp(
        strategy_class="EARN",
        timeframe_bucket="intraday",
        recent_r_multiples=[-0.6] * 8,  # sum -4.8, mean -0.6 < threshold applied to sum -4
        dwell=0,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.reason == "TRIPWIRE"


def test_tripwire_swing_treated_like_intraday() -> None:
    inp = _inp(
        strategy_class="EARN",
        timeframe_bucket="swing",
        recent_r_multiples=[-0.6] * 8,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.reason == "TRIPWIRE"


def test_tripwire_1d_plus_last5() -> None:
    inp = _inp(
        strategy_class="EARN",
        timeframe_bucket="1d_plus",
        recent_r_multiples=[-0.7] * 5,  # sum -3.5 < -3
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.reason == "TRIPWIRE"


def test_tripwire_does_not_fire_when_not_severe_enough() -> None:
    inp = _inp(
        strategy_class="EARN",
        timeframe_bucket="intraday",
        recent_r_multiples=[-0.4] * 8,  # sum -3.2, above -4 threshold
        intent_scores=[1.0] * 20,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"


def test_tripwire_ignores_dwell_lock() -> None:
    # PROVE with dwell far below 10 would normally be DWELL_LOCKED for any
    # transition — tripwire must still fire immediately.
    inp = _inp(
        strategy_class="PROVE",
        dwell=1,
        timeframe_bucket="1d_plus",
        recent_r_multiples=[-1.0] * 5,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.reason == "TRIPWIRE"


def test_tripwire_beats_kill_priority_ordering_is_kill_first() -> None:
    # Confirms priority: KILL is still checked before tripwire (KILL wins).
    inp = _inp(
        strategy_class="EARN",
        kill_state="KILLED",
        timeframe_bucket="intraday",
        recent_r_multiples=[-1.0] * 8,
    )
    result = evaluate_transition(inp)
    assert result.reason == "KILL_NO_AUTO_REVIVE"


# ---------------------------------------------------------------------------
# PROVE stagnation
# ---------------------------------------------------------------------------


def test_prove_stagnation_to_bench() -> None:
    inp = _inp(
        strategy_class="PROVE",
        window_w=20,
        dwell=3,
        intent_scores=[1.5] * 20,  # full-W mean +1.5 <= +2 stagnation threshold
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.reason == "PROVE_STAGNATION"


def test_prove_not_stagnant_above_threshold() -> None:
    inp = _inp(
        strategy_class="PROVE",
        window_w=20,
        dwell=3,
        intent_scores=[3.0] * 20,  # mean +3 > +2, not stagnant
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "PROVE"


def test_prove_stagnation_requires_full_window_sample() -> None:
    inp = _inp(
        strategy_class="PROVE",
        window_w=20,
        dwell=3,
        intent_scores=[1.0] * 5,  # fewer than W closes yet — no verdict
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "PROVE"
    assert result.reason != "PROVE_STAGNATION"


# ---------------------------------------------------------------------------
# fill gate (3-tier)
# ---------------------------------------------------------------------------


def test_fill_gate_off_under_10_does_not_block_schmitt() -> None:
    scores = [1.0] * 12 + [-5.0] * 8
    inp = _inp(
        strategy_class="EARN",
        window_w=20,
        intent_scores=scores,
        n_fills_total=9,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"


def test_fill_gate_10_49_blocks_when_under_threshold() -> None:
    # n=30 -> max(3, ceil(0.2*30))=6 required fills; only 2 achieved
    scores = [1.0] * 12 + [-5.0] * 8
    inp = _inp(
        strategy_class="EARN",
        window_w=20,
        intent_scores=scores,
        n_fills_total=30,
        n_signals_recent=30,
        n_fills_recent=2,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"
    assert result.reason == "EXEC_STARVED"


def test_fill_gate_10_49_passes_when_threshold_met() -> None:
    scores = [1.0] * 12 + [-5.0] * 8
    inp = _inp(
        strategy_class="EARN",
        window_w=20,
        intent_scores=scores,
        n_fills_total=30,
        n_signals_recent=30,
        n_fills_recent=6,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"


def test_fill_gate_10_49_minimum_floor_is_3() -> None:
    # n=12 -> ceil(0.2*12)=3 (floor already 3) — need >=3
    scores = [1.0] * 12 + [-5.0] * 8
    inp = _inp(
        strategy_class="EARN",
        window_w=20,
        intent_scores=scores,
        n_fills_total=12,
        n_signals_recent=12,
        n_fills_recent=2,
    )
    result = evaluate_transition(inp)
    assert result.reason == "EXEC_STARVED"


def test_fill_gate_50_plus_blocks_under_rate() -> None:
    scores = [1.0] * 12 + [-5.0] * 8
    inp = _inp(
        strategy_class="EARN",
        window_w=20,
        intent_scores=scores,
        n_fills_total=60,
        last_50_fill_rate=0.10,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"
    assert result.reason == "EXEC_STARVED"


def test_fill_gate_50_plus_passes_at_rate_boundary() -> None:
    scores = [1.0] * 12 + [-5.0] * 8
    inp = _inp(
        strategy_class="EARN",
        window_w=20,
        intent_scores=scores,
        n_fills_total=60,
        last_50_fill_rate=0.20,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"


def test_fill_gate_blocks_tripwire_too() -> None:
    inp = _inp(
        strategy_class="EARN",
        timeframe_bucket="1d_plus",
        recent_r_multiples=[-1.0] * 5,
        n_fills_total=60,
        last_50_fill_rate=0.05,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"
    assert result.reason == "EXEC_STARVED"


# ---------------------------------------------------------------------------
# promotion rate limit — 1/24h, demotion unlimited
# ---------------------------------------------------------------------------


def test_promotion_blocked_within_24h_of_last_promotion() -> None:
    inp = _inp(
        strategy_class="PROVE",
        dwell=10,
        window_w=20,
        intent_scores=[5.0] * 20,
        last_promotion_ts=BASE_TS,
        now_ts=BASE_TS + DAY - 1,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "PROVE"
    assert result.reason == "PROMOTION_RATE_LIMITED"


def test_promotion_allowed_after_24h() -> None:
    inp = _inp(
        strategy_class="PROVE",
        dwell=10,
        window_w=20,
        intent_scores=[5.0] * 20,
        last_promotion_ts=BASE_TS,
        now_ts=BASE_TS + DAY,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"


def test_demotion_not_rate_limited_within_24h() -> None:
    scores = [1.0] * 12 + [-5.0] * 8
    inp = _inp(
        strategy_class="EARN",
        window_w=20,
        intent_scores=scores,
        last_promotion_ts=BASE_TS,
        now_ts=BASE_TS + 60,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"


# ---------------------------------------------------------------------------
# reentry ladder (BENCH -> PROVE via shadow_ring, pessimistic)
# ---------------------------------------------------------------------------


def test_ladder_step0_promotes_on_1w_shadow_closes_and_score() -> None:
    inp = _inp(
        strategy_class="BENCH",
        ladder_step=0,
        window_w=20,
        shadow_scores=[3.5] * 20,  # 1.0*W=20 closes, mean >= +3
    )
    result = evaluate_transition(inp)
    assert result.ladder_step == 1
    assert result.reason == "LADDER_STEP_UP"


def test_ladder_step0_holds_below_sample_requirement() -> None:
    inp = _inp(
        strategy_class="BENCH",
        ladder_step=0,
        window_w=20,
        shadow_scores=[10.0] * 15,  # < 20 closes yet
    )
    result = evaluate_transition(inp)
    assert result.ladder_step == 0


def test_ladder_step1_promotes_on_2w_shadow_closes_and_score() -> None:
    inp = _inp(
        strategy_class="BENCH",
        ladder_step=1,
        window_w=20,
        shadow_scores=[6.5] * 40,  # 2.0*W=40, mean >= +6
    )
    result = evaluate_transition(inp)
    assert result.ladder_step == 2
    assert result.reason == "LADDER_STEP_UP"


def test_ladder_step2_promotes_out_of_ladder_into_prove() -> None:
    inp = _inp(
        strategy_class="BENCH",
        ladder_step=2,
        window_w=20,
        shadow_scores=[9.5] * 60,  # 3.0*W=60, mean >= +9 -> cap, exit to PROVE
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "PROVE"
    assert result.ladder_step == 0
    assert result.reason == "LADDER_EXIT_TO_PROVE"


def test_ladder_decay_drops_step_on_weak_shadow() -> None:
    inp = _inp(
        strategy_class="BENCH",
        ladder_step=1,
        window_w=20,
        shadow_scores=[1.0] * 20,  # 1.0*W sample, mean < +3 decay threshold
    )
    result = evaluate_transition(inp)
    assert result.ladder_step == 0
    assert result.reason == "LADDER_DECAY"


def test_ladder_decay_at_step0_holds_at_zero() -> None:
    inp = _inp(
        strategy_class="BENCH",
        ladder_step=0,
        window_w=20,
        shadow_scores=[1.0] * 20,
    )
    result = evaluate_transition(inp)
    assert result.ladder_step == 0
    assert result.strategy_class == "BENCH"


def test_ladder_step_up_wins_over_conflicting_recent_decay_dip() -> None:
    # step1's own window (2.0*W=40) clears its +6 threshold even though the
    # more-recent 1.0*W=20 slice alone would read as a decay dip (<+3) —
    # step-up's larger-sample verdict wins.
    scores = [20.0] * 20 + [1.0] * 20
    inp = _inp(strategy_class="BENCH", ladder_step=1, window_w=20, shadow_scores=scores)
    result = evaluate_transition(inp)
    assert result.ladder_step == 2
    assert result.reason == "LADDER_STEP_UP"


def test_ladder_only_applies_to_bench() -> None:
    inp = _inp(
        strategy_class="EARN",
        ladder_step=0,
        window_w=20,
        shadow_scores=[10.0] * 20,
        intent_scores=[1.0] * 20,
    )
    result = evaluate_transition(inp)
    assert result.strategy_class == "EARN"
    assert result.ladder_step == 0


# ---------------------------------------------------------------------------
# hysteresis oscillation guard — dwell must prevent rapid flip-flop
# ---------------------------------------------------------------------------


def test_no_oscillation_immediately_after_prove_to_earn() -> None:
    # Right after promotion, a single-window borderline negative score should
    # not immediately re-demote given dwell semantics reset on transition —
    # exercised by re-running evaluate_transition on the promoted result with
    # dwell freshly reset by the caller (0) and no severe score.
    inp = _inp(
        strategy_class="PROVE",
        dwell=10,
        window_w=20,
        intent_scores=[5.0] * 20,
        last_promotion_ts=None,
    )
    promoted = evaluate_transition(inp)
    assert promoted.strategy_class == "EARN"
    assert promoted.dwell == 0
    assert promoted.epoch_id == inp.epoch_id + 1


def test_promotion_bumps_epoch_when_notional_or_r_alloc_grows() -> None:
    inp = _inp(
        strategy_class="PROVE",
        dwell=10,
        window_w=20,
        intent_scores=[5.0] * 20,
        epoch_id=3,
        median_notional_ratio=1.8,
        r_alloc_ratio=1.0,
    )
    result = evaluate_transition(inp)
    assert result.epoch_id == 4


def test_no_epoch_bump_on_demotion() -> None:
    scores = [1.0] * 12 + [-5.0] * 8
    inp = _inp(strategy_class="EARN", window_w=20, intent_scores=scores, epoch_id=3)
    result = evaluate_transition(inp)
    assert result.strategy_class == "BENCH"
    assert result.epoch_id == 3


def test_bad_class_raises() -> None:
    with pytest.raises(ValueError):
        _inp(strategy_class="NOT_A_CLASS")


# ---------------------------------------------------------------------------
# dwell preserved (not reset) whenever no actual transition fires
# ---------------------------------------------------------------------------


def test_dwell_preserved_on_dwell_locked() -> None:
    inp = _inp(strategy_class="PROVE", dwell=9, window_w=20, intent_scores=[5.0] * 20)
    result = evaluate_transition(inp)
    assert result.reason == "DWELL_LOCKED"
    assert result.dwell == 9


def test_dwell_preserved_on_exec_starved() -> None:
    scores = [1.0] * 12 + [-5.0] * 8
    inp = _inp(
        strategy_class="EARN",
        dwell=7,
        window_w=20,
        intent_scores=scores,
        n_fills_total=60,
        last_50_fill_rate=0.05,
    )
    result = evaluate_transition(inp)
    assert result.reason == "EXEC_STARVED"
    assert result.dwell == 7


def test_dwell_preserved_on_promotion_rate_limited() -> None:
    inp = _inp(
        strategy_class="PROVE",
        dwell=10,
        window_w=20,
        intent_scores=[5.0] * 20,
        last_promotion_ts=BASE_TS,
        now_ts=BASE_TS + 60,
    )
    result = evaluate_transition(inp)
    assert result.reason == "PROMOTION_RATE_LIMITED"
    assert result.dwell == 10
