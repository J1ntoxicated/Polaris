"""D2 — expectancy-aware learner scoring (TDD).

WR-alone promotion lets a high-WR / negative-expectancy strategy claim full
size: many small wins + a few large losses -> WR>=0.55 but expectancy<0. The
session/regime learners must fold EXPECTANCY (mean pnl_r) into the score so a
negative-expectancy bucket does NOT get a +0.1 promotion.

flow_not_block: this changes the SCORE only. It does NOT add a defensive floor,
a size cut, or a block. A positive-expectancy high-WR bucket is promoted exactly
as before (aggressive bias preserved); only the high-WR-but-LOSING bucket loses
its undeserved promotion.
"""

from __future__ import annotations

from polaris.core.learners._primitives import NEUTRAL_MULT
from polaris.core.learners.regime import RegimeMultLearner
from polaris.core.learners.session import SessionMultLearner


def _stats(*, n: float, wins: float, pnl_sum: float, value: float = NEUTRAL_MULT) -> dict[str, float]:
    return {
        "value": value,
        "n_eff": n,
        "wins_eff": wins,
        "pnl_r_sum_eff": pnl_sum,
        "pending_delta": 0.0,
    }


def test_high_wr_positive_expectancy_still_promotes() -> None:
    # WR=0.60 (>=0.55) AND expectancy=+0.20 -> deserved promotion preserved.
    learner = SessionMultLearner.__new__(SessionMultLearner)
    stats = _stats(n=30, wins=18, pnl_sum=6.0)  # exp = 6.0/30 = +0.20
    assert learner.compute_value_from_stats(stats) == 1.1


def test_high_wr_negative_expectancy_does_not_promote() -> None:
    # WR=0.60 (>=0.55) BUT expectancy=-0.10 (net losing) -> NO promotion.
    # WR-alone would have returned 1.1; expectancy-aware holds at neutral.
    learner = SessionMultLearner.__new__(SessionMultLearner)
    stats = _stats(n=30, wins=18, pnl_sum=-3.0)  # exp = -3.0/30 = -0.10
    assert learner.compute_value_from_stats(stats) == NEUTRAL_MULT


def test_high_wr_negative_expectancy_does_not_promote_regime() -> None:
    learner = RegimeMultLearner.__new__(RegimeMultLearner)
    stats = _stats(n=40, wins=26, pnl_sum=-4.0)  # WR=0.65, exp=-0.10
    assert learner.compute_value_from_stats(stats) == NEUTRAL_MULT


def test_low_wr_demotes_unchanged() -> None:
    # Demotion path is WR-driven and unchanged (losing + low WR -> -0.1).
    learner = SessionMultLearner.__new__(SessionMultLearner)
    stats = _stats(n=30, wins=10, pnl_sum=-5.0)  # WR=0.33 (<=0.40)
    assert learner.compute_value_from_stats(stats) == 0.9


def test_sparse_unchanged() -> None:
    # n_eff < 20 -> unchanged (sparse fallback), expectancy irrelevant.
    learner = SessionMultLearner.__new__(SessionMultLearner)
    stats = _stats(n=10, wins=8, pnl_sum=5.0, value=1.2)
    assert learner.compute_value_from_stats(stats) == 1.2


def test_promotion_is_not_a_floor_or_cut() -> None:
    # The expectancy guard only WITHHOLDS an undeserved promotion (returns the
    # current value); it never pushes BELOW the current value -> no defensive
    # size cut introduced by this change.
    learner = SessionMultLearner.__new__(SessionMultLearner)
    # high WR, negative expectancy, current value already elevated to 1.3:
    stats = _stats(n=30, wins=18, pnl_sum=-3.0, value=1.3)
    out = learner.compute_value_from_stats(stats)
    assert out == 1.3  # held, NOT cut below current
