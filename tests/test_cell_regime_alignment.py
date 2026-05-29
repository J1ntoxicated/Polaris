"""TDD — regime→cell-matrix score alignment (MVP, deterministic vol/trend fit).

Spec source: task #9 — couple regime detection into the cell-matrix score input.
Trend strategies (tsmom / spot_donchian / breakout family) weighted UP in trend
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
    apply_regime_alignment,
    regime_alignment_mult,
)

TREND_STRATEGIES = (
    "tsmom",
    "spot_donchian",
    "fx_breakout_basket",
    "session_breakout",
    "xau_indices_trend",
    "volume_burst",
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
    assert regime_alignment_mult(strategy="tsmom", regime="sideways") == REGIME_ALIGN_NEUTRAL


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
    out = apply_regime_alignment(base, strategy="tsmom", regime="bull_trend")
    assert out == base * REGIME_ALIGN_AMPLIFY
    assert out > base


def test_apply_dampens_positive_score_in_misaligned_regime() -> None:
    base = 0.8
    out = apply_regime_alignment(base, strategy="tsmom", regime="chop")
    assert out == base * REGIME_ALIGN_DAMPEN
    assert 0.0 < out < base


def test_apply_preserves_negative_sign() -> None:
    # A losing cell stays losing — amplify must not flip a negative to positive
    # nor dampen lift it toward zero past the router's reach.
    base = -0.5
    amp = apply_regime_alignment(base, strategy="tsmom", regime="bull_trend")
    damp = apply_regime_alignment(base, strategy="tsmom", regime="chop")
    assert amp < 0.0
    assert damp < 0.0
    assert amp < base < damp  # amplify pushes more negative, dampen toward zero


def test_apply_neutral_is_identity() -> None:
    base = 0.42
    assert apply_regime_alignment(base, strategy="tsmom", regime="crisis") == base


def test_apply_zero_score_stays_zero() -> None:
    for regime in ("bull_trend", "chop", "crisis"):
        assert apply_regime_alignment(0.0, strategy="tsmom", regime=regime) == 0.0


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
