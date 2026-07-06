"""TDD — regime→cell-matrix score alignment (MVP, deterministic vol/trend fit).

Spec source: task #9 — couple regime detection into the cell-matrix score input.
Trend strategies (xau_indices_trend / breakout family) weighted UP in trend
regimes; counter-trend (rsi_bb_pullback) weighted UP in chop. This is a
REDISTRIBUTION (amplify aligned / dampen misaligned) — never an off-switch, and
never below the dampen floor. Aggressive bias preserved.
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from polaris.core.cell_matrix.score import (
    REGIME_ALIGN_AMPLIFY,
    REGIME_ALIGN_DAMPEN,
    REGIME_ALIGN_NEUTRAL,
    REGIME_RANK_PENALTY_DAMPEN,
    REGIME_RANK_PENALTY_NEUTRAL,
    apply_regime_alignment,
    regime_alignment_mult,
    regime_rank_penalty,
)

# volume_burst un-registered 2026-06-27 (#61 live-churn KILL) + spot_donchian
# un-registered 2026-06-27 (#56 stop-bleeders KILL) + session_breakout
# un-registered 2026-07-06 (B1 prune, live-ledger forensic, -$933.65
# fee-bleed) — all removed from the live trend-strategy exemplars (none
# scores via _TREND_STRATEGIES anymore).
TREND_STRATEGIES = (
    "fx_breakout_basket",
    "xau_indices_trend",
)
TREND_REGIMES = ("bull_trend", "bear_trend")


# ---------------------------------------------------------------------------
# Alignment multiplier — deterministic regime/strategy fit
# ---------------------------------------------------------------------------


def test_trend_strategy_amplified_in_trend_regime() -> None:
    for strat in TREND_STRATEGIES:
        for regime in TREND_REGIMES:
            assert regime_alignment_mult(strategy=strat, regime=regime) == REGIME_ALIGN_AMPLIFY


def test_trend_strategy_dampened_in_chop() -> None:
    for strat in TREND_STRATEGIES:
        assert regime_alignment_mult(strategy=strat, regime="chop") == REGIME_ALIGN_DAMPEN


def test_counter_trend_amplified_in_chop() -> None:
    assert regime_alignment_mult(strategy="rsi_bb_pullback", regime="chop") == REGIME_ALIGN_AMPLIFY


def test_counter_trend_dampened_in_trend() -> None:
    for regime in TREND_REGIMES:
        assert (
            regime_alignment_mult(strategy="rsi_bb_pullback", regime=regime)
            == REGIME_ALIGN_DAMPEN
        )


def test_crisis_regime_neutral_for_all() -> None:
    # Crisis is not a directional edge signal — no alignment claim, stay neutral.
    for strat in (*TREND_STRATEGIES, "rsi_bb_pullback"):
        assert regime_alignment_mult(strategy=strat, regime="crisis") == REGIME_ALIGN_NEUTRAL


def test_unknown_regime_neutral() -> None:
    assert regime_alignment_mult(strategy="xau_indices_trend", regime="sideways") == REGIME_ALIGN_NEUTRAL


def test_unknown_strategy_neutral() -> None:
    assert regime_alignment_mult(strategy="mystery_alpha", regime="bull_trend") == (
        REGIME_ALIGN_NEUTRAL
    )


def test_dampen_is_redistribution_not_off() -> None:
    # Dampen must stay strictly positive (a misaligned cell is down-weighted,
    # never excluded). Aggressive bias: no off-switch.
    assert REGIME_ALIGN_DAMPEN > 0.0
    assert REGIME_ALIGN_DAMPEN < REGIME_ALIGN_NEUTRAL < REGIME_ALIGN_AMPLIFY


# ---------------------------------------------------------------------------
# apply_regime_alignment — scales a cell score, preserves sign + contract
# ---------------------------------------------------------------------------


def test_apply_amplifies_positive_score_in_aligned_regime() -> None:
    base = 0.8
    out = apply_regime_alignment(base, strategy="xau_indices_trend", regime="bull_trend")
    assert out == base * REGIME_ALIGN_AMPLIFY
    assert out > base


def test_apply_dampens_positive_score_in_misaligned_regime() -> None:
    base = 0.8
    out = apply_regime_alignment(base, strategy="xau_indices_trend", regime="chop")
    assert out == base * REGIME_ALIGN_DAMPEN
    assert 0.0 < out < base


def test_apply_preserves_negative_sign() -> None:
    # A losing cell stays losing — amplify must not flip a negative to positive
    # nor dampen lift it toward zero past the router's reach.
    base = -0.5
    amp = apply_regime_alignment(base, strategy="xau_indices_trend", regime="bull_trend")
    damp = apply_regime_alignment(base, strategy="xau_indices_trend", regime="chop")
    assert amp < 0.0
    assert damp < 0.0
    assert amp < base < damp  # amplify pushes more negative, dampen toward zero


def test_apply_neutral_is_identity() -> None:
    base = 0.42
    assert apply_regime_alignment(base, strategy="xau_indices_trend", regime="crisis") == base


def test_apply_zero_score_stays_zero() -> None:
    for regime in ("bull_trend", "chop", "crisis"):
        assert apply_regime_alignment(0.0, strategy="xau_indices_trend", regime=regime) == 0.0


@given(
    base=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    strategy=st.sampled_from((*TREND_STRATEGIES, "rsi_bb_pullback", "mystery")),
    regime=st.sampled_from(("bull_trend", "bear_trend", "chop", "crisis", "weird")),
)
def test_property_apply_finite_and_sign_consistent(
    base: float, strategy: str, regime: str
) -> None:
    out = apply_regime_alignment(base, strategy=strategy, regime=regime)
    assert math.isfinite(out)
    # Sign never flips (multiplier strictly positive).
    if base > 0.0:
        assert out > 0.0
    elif base < 0.0:
        assert out < 0.0
    else:
        assert out == 0.0


# ---------------------------------------------------------------------------
# regime_rank_penalty — ticker-tailored G2 emit-priority (regime-first POOL).
#
# The same (strategy × THIS-ticker's-live-regime) fitness, re-expressed as a
# NON-NEGATIVE ranking-down penalty (the PipelineTaskSpec.rank_penalty contract):
# a well-fit strategy on a ticker whose live regime suits it RANKS AHEAD (lower
# penalty = sorted earlier in the per-tick batch = a head-start into the
# concurrent gate fan-out, NOT a guaranteed budget-reservation order) of a mis-fit
# one. NEVER a block, NEVER a size-cut, NEVER a T4 multiplier — pure batch ORDER
# (flow_not_block, 9-stack untouched). Cold/crisis/unknown → neutral (the
# cold-start trap the debate flagged: an unclassified ticker is never demoted
# into oblivion).
# ---------------------------------------------------------------------------


def test_rank_penalty_aligned_is_zero_sorts_first() -> None:
    # A trend strategy on a ticker in a trend regime is the best-fit POOL member
    # → the LOWEST penalty (0.0) = sorted first in the per-tick batch.
    for strat in TREND_STRATEGIES:
        for regime in TREND_REGIMES:
            assert regime_rank_penalty(strategy=strat, regime=regime) == 0.0
    assert regime_rank_penalty(strategy="rsi_bb_pullback", regime="chop") == 0.0


def test_rank_penalty_misaligned_ranks_down_but_positive_finite() -> None:
    # A mis-regime strategy is RANKED DOWN (positive penalty) — still emits,
    # still flows, still sized normally — just later in the batch.
    for strat in TREND_STRATEGIES:
        pen = regime_rank_penalty(strategy=strat, regime="chop")
        assert pen == REGIME_RANK_PENALTY_DAMPEN
        assert pen > 0.0
        assert math.isfinite(pen)
    for regime in TREND_REGIMES:
        assert (
            regime_rank_penalty(strategy="rsi_bb_pullback", regime=regime)
            == REGIME_RANK_PENALTY_DAMPEN
        )


def test_rank_penalty_neutral_between_aligned_and_misaligned() -> None:
    # Crisis / unknown regime / unknown strategy → neutral penalty, strictly
    # BETWEEN best-fit (0.0) and mis-fit (dampen). Ordering: aligned < neutral
    # < misaligned, so the regime-first POOL is a 3-tier priority, never a gate.
    neutral = regime_rank_penalty(strategy="xau_indices_trend", regime="crisis")
    assert neutral == REGIME_RANK_PENALTY_NEUTRAL
    assert 0.0 < REGIME_RANK_PENALTY_NEUTRAL < REGIME_RANK_PENALTY_DAMPEN


def test_rank_penalty_cold_unknown_never_demoted_to_oblivion() -> None:
    # Unknown strategy / unknown regime → neutral (NOT the worst tier): the
    # cold-start trap. A fresh ticker/strategy competes on neutral footing.
    assert regime_rank_penalty(strategy="mystery_alpha", regime="bull_trend") == (
        REGIME_RANK_PENALTY_NEUTRAL
    )
    assert regime_rank_penalty(strategy="xau_indices_trend", regime="sideways") == (
        REGIME_RANK_PENALTY_NEUTRAL
    )


def test_rank_penalty_is_below_one_pdt_step_so_pdt_dominates() -> None:
    # All regime penalties live UNDER one PDT step (1.0) so a PDT-flagged equity
    # entry (integrity, penalty >= 1.0) always sinks below any non-PDT entry
    # regardless of regime fit — integrity dominates, regime fit is secondary.
    assert REGIME_RANK_PENALTY_DAMPEN < 1.0
    assert REGIME_RANK_PENALTY_NEUTRAL < 1.0


def test_rank_penalty_mirrors_alignment_mult_ordering() -> None:
    # The penalty is a re-encoding of the SAME fitness SSOT: a strategy with a
    # higher alignment mult must never have a HIGHER penalty (monotone inverse).
    cases = [
        ("xau_indices_trend", "bull_trend"),
        ("xau_indices_trend", "chop"),
        ("xau_indices_trend", "crisis"),
        ("rsi_bb_pullback", "chop"),
        ("rsi_bb_pullback", "bull_trend"),
        ("mystery", "bull_trend"),
    ]
    for strat, regime in cases:
        mult = regime_alignment_mult(strategy=strat, regime=regime)
        pen = regime_rank_penalty(strategy=strat, regime=regime)
        if mult == REGIME_ALIGN_AMPLIFY:
            assert pen == 0.0
        elif mult == REGIME_ALIGN_NEUTRAL:
            assert pen == REGIME_RANK_PENALTY_NEUTRAL
        else:  # dampen
            assert pen == REGIME_RANK_PENALTY_DAMPEN


@given(
    strategy=st.sampled_from((*TREND_STRATEGIES, "rsi_bb_pullback", "mystery")),
    regime=st.sampled_from(("bull_trend", "bear_trend", "chop", "crisis", "weird")),
)
def test_property_rank_penalty_non_negative_finite_bounded(
    strategy: str, regime: str
) -> None:
    pen = regime_rank_penalty(strategy=strategy, regime=regime)
    assert math.isfinite(pen)
    assert 0.0 <= pen <= REGIME_RANK_PENALTY_DAMPEN  # never a block / sentinel


# ---------------------------------------------------------------------------
# P-A — venue/side-aware bear_trend headwind for long-only trend strategies.
#
# bear_trend is a TREND regime, so the family rule AMPLIFIES every trend
# strategy there. But a long-only venue (OKX SPOT, Alpaca equity) can ONLY go
# long: a trend long in a DOWN-trend is HEADWIND, not edge. So in bear_trend a
# trend strategy on a long-only venue is DAMPENED (0.8, strictly-positive
# redistribution — "going to cash is the edge", never 0 / never blocked).
# Capital CFD is long/short, so its trend strategies KEEP AMPLIFY in bear_trend
# (the short leg is the bear edge). bull_trend / chop / crisis are unchanged.
# ---------------------------------------------------------------------------

LONG_ONLY_VENUES = ("okx", "alpaca")
# volume_burst un-registered 2026-06-27 (#61 — live-churn KILL); removed here
# so this set lists only live long-only trend strategies.
LONG_ONLY_TREND_STRATEGIES = ("xau_indices_trend",)
# session_breakout un-registered 2026-07-06 (B1 prune, live-ledger forensic,
# -$933.65 fee-bleed) — removed here so this set lists only live Capital
# trend strategies.
CAPITAL_TREND_STRATEGIES = (
    "fx_breakout_basket",
    "xau_indices_trend",
)


def test_bear_trend_long_only_venue_trend_dampened() -> None:
    # The fix: a long-only-venue trend strategy is DAMPENED in bear_trend
    # (long-only = no short leg, so a trend long is headwind, not edge).
    for venue in LONG_ONLY_VENUES:
        for strat in LONG_ONLY_TREND_STRATEGIES:
            assert (
                regime_alignment_mult(
                    strategy=strat, regime="bear_trend", exchange=venue
                )
                == REGIME_ALIGN_DAMPEN
            )


def test_bear_trend_capital_trend_amplified() -> None:
    # Capital is long/short — its trend strategies short the bear, so AMPLIFY
    # stays. (The short leg IS the bear edge; do NOT dampen it.)
    for strat in (*CAPITAL_TREND_STRATEGIES, "fx_breakout_basket"):
        assert (
            regime_alignment_mult(
                strategy=strat, regime="bear_trend", exchange="capital"
            )
            == REGIME_ALIGN_AMPLIFY
        )


def test_bull_trend_long_only_venue_trend_amplified_invariant() -> None:
    # Only bear_trend changes — a long-only trend long in an UP-trend is the
    # tailwind, so bull_trend stays AMPLIFY for every venue.
    for venue in (*LONG_ONLY_VENUES, "capital"):
        for strat in LONG_ONLY_TREND_STRATEGIES:
            assert (
                regime_alignment_mult(
                    strategy=strat, regime="bull_trend", exchange=venue
                )
                == REGIME_ALIGN_AMPLIFY
            )


def test_chop_long_only_venue_trend_dampened_invariant() -> None:
    # chop already DAMPENS trend regardless of venue — unchanged by P-A.
    for venue in (*LONG_ONLY_VENUES, "capital"):
        for strat in LONG_ONLY_TREND_STRATEGIES:
            assert (
                regime_alignment_mult(strategy=strat, regime="chop", exchange=venue)
                == REGIME_ALIGN_DAMPEN
            )


def test_bear_long_only_dampen_is_strictly_positive_redistribution() -> None:
    # flow_not_block: the bear headwind is a DAMPEN (0.8), strictly positive —
    # never 0, never a block. A losing/winning long signal is down-weighted,
    # never excluded.
    mult = regime_alignment_mult(
        strategy="xau_indices_trend", regime="bear_trend", exchange="okx"
    )
    assert mult == REGIME_ALIGN_DAMPEN
    assert mult > 0.0


def test_no_exchange_defaults_to_amplify_in_bear_backward_compat() -> None:
    # No venue supplied = no venue-specific headwind claim → preserves the
    # pre-P-A family behavior (AMPLIFY in bear_trend). The production routing /
    # emit-priority call sites DO pass exchange, so the fix fires there.
    for strat in LONG_ONLY_TREND_STRATEGIES:
        assert (
            regime_alignment_mult(strategy=strat, regime="bear_trend")
            == REGIME_ALIGN_AMPLIFY
        )


def test_counter_trend_bear_unchanged_by_venue() -> None:
    # Counter-trend is already misaligned in any trend regime (DAMPEN) — venue
    # does not change that (the P-A headwind only touches the trend family).
    for venue in (*LONG_ONLY_VENUES, "capital", None):
        assert (
            regime_alignment_mult(
                strategy="rsi_bb_pullback", regime="bear_trend", exchange=venue
            )
            == REGIME_ALIGN_DAMPEN
        )


def test_apply_regime_alignment_threads_exchange() -> None:
    base = 0.8
    okx_out = apply_regime_alignment(
        base, strategy="xau_indices_trend", regime="bear_trend", exchange="okx"
    )
    cap_out = apply_regime_alignment(
        base, strategy="fx_breakout_basket", regime="bear_trend", exchange="capital"
    )
    assert okx_out == base * REGIME_ALIGN_DAMPEN
    assert 0.0 < okx_out < base  # dampened, strictly positive (flow_not_block)
    assert cap_out == base * REGIME_ALIGN_AMPLIFY  # capital short keeps amplify


def test_rank_penalty_threads_exchange_bear_long_only_ranks_down() -> None:
    # The emit-priority penalty re-derives from the same SSOT: a long-only bear
    # trend now ranks DOWN (dampen penalty), while Capital bear trend ranks
    # first (amplify → 0). Still emits / flows / sized — pure batch ORDER.
    okx_pen = regime_rank_penalty(
        strategy="xau_indices_trend", regime="bear_trend", exchange="okx"
    )
    cap_pen = regime_rank_penalty(
        strategy="fx_breakout_basket", regime="bear_trend", exchange="capital"
    )
    assert okx_pen == REGIME_RANK_PENALTY_DAMPEN
    assert cap_pen == 0.0


@given(
    venue=st.sampled_from(("okx", "alpaca", "capital", "unknown_venue", None)),
    strategy=st.sampled_from(
        (*LONG_ONLY_TREND_STRATEGIES, *CAPITAL_TREND_STRATEGIES, "rsi_bb_pullback")
    ),
    regime=st.sampled_from(("bull_trend", "bear_trend", "chop", "crisis", "weird")),
)
def test_property_venue_aware_mult_strictly_positive_bounded(
    venue: str | None, strategy: str, regime: str
) -> None:
    mult = regime_alignment_mult(strategy=strategy, regime=regime, exchange=venue)
    assert math.isfinite(mult)
    # Strictly positive, never an off-switch, never above the amplify ceiling.
    assert REGIME_ALIGN_DAMPEN <= mult <= REGIME_ALIGN_AMPLIFY
    assert mult > 0.0
