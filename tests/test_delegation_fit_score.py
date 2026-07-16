"""TDD — delegation-gate fit-score (P2b, SHADOW v1, pure/deterministic).

Design SSOT: vault/50_research/delegation-gate-blueprint.md.
"""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from polaris.core.delegation.fit_score import (
    AMBIGUOUS_MARGIN_THRESHOLD,
    HISTORY_NORM_BPS,
    fit_score,
    margin_of,
    rank_strategies,
    regime_fit_score,
)
from polaris.strategies.base import StrategyMetadata

# ---------------------------------------------------------------------------
# regime_fit_score
# ---------------------------------------------------------------------------


def test_trend_edge_amplified_in_trend_regime() -> None:
    assert regime_fit_score("trend", "bull_trend") == 1.0
    assert regime_fit_score("trend", "bear_trend") == 1.0
    assert regime_fit_score("breakout", "bull_trend") == 1.0


def test_trend_edge_dampened_in_chop() -> None:
    assert regime_fit_score("trend", "chop") == -1.0
    assert regime_fit_score("breakout", "chop") == -1.0


def test_reversion_edge_amplified_in_chop_dampened_in_trend() -> None:
    assert regime_fit_score("reversion", "chop") == 1.0
    assert regime_fit_score("reversion", "bull_trend") == -1.0
    assert regime_fit_score("reversion", "bear_trend") == -1.0


def test_crisis_and_unknown_stay_neutral() -> None:
    for edge in ("trend", "reversion", "breakout"):
        assert regime_fit_score(edge, "crisis") == 0.0
    assert regime_fit_score("unknown", "bull_trend") == 0.0
    assert regime_fit_score("unknown", "chop") == 0.0


# ---------------------------------------------------------------------------
# fit_score — weighted sum
# ---------------------------------------------------------------------------


def test_fit_score_rewards_matching_asset_class_and_regime_and_history() -> None:
    best = fit_score(
        strategy_id="xau_indices_trend",
        correlation_group_id="cfd_index_commodity_trend",
        strategy_asset_class="commodity",
        ticker_asset_class="commodity",
        regime="bull_trend",
        historical_edge_bps=50.0,
    )
    worst = fit_score(
        strategy_id="xau_indices_trend",
        correlation_group_id="cfd_index_commodity_trend",
        strategy_asset_class="commodity",
        ticker_asset_class="fx",
        regime="chop",
        historical_edge_bps=-50.0,
    )
    assert best > worst
    assert best > 0.0
    assert worst < 0.0


def test_historical_edge_is_clamped_to_unit_range() -> None:
    huge = fit_score(
        strategy_id="s", correlation_group_id="", strategy_asset_class="",
        ticker_asset_class="", regime="crisis",
        historical_edge_bps=HISTORY_NORM_BPS * 100.0,
    )
    at_norm = fit_score(
        strategy_id="s", correlation_group_id="", strategy_asset_class="",
        ticker_asset_class="", regime="crisis",
        historical_edge_bps=HISTORY_NORM_BPS,
    )
    assert huge == at_norm  # clamped, not unbounded


def test_no_history_is_neutral_not_punished() -> None:
    cold = fit_score(
        strategy_id="s", correlation_group_id="", strategy_asset_class="",
        ticker_asset_class="", regime="crisis", historical_edge_bps=0.0,
    )
    assert cold == 0.0


@given(
    historical_edge_bps=st.floats(
        min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
    ),
    regime=st.sampled_from(("bull_trend", "bear_trend", "chop", "crisis", "weird")),
    strategy_asset_class=st.sampled_from(("commodity", "fx", "", "crypto")),
    ticker_asset_class=st.sampled_from(("commodity", "fx", "", "crypto")),
)
def test_property_fit_score_finite_and_bounded(
    historical_edge_bps: float,
    regime: str,
    strategy_asset_class: str,
    ticker_asset_class: str,
) -> None:
    out = fit_score(
        strategy_id="tsmom_12_1_multiasset",
        correlation_group_id="tsmom_position_momentum",
        strategy_asset_class=strategy_asset_class,
        ticker_asset_class=ticker_asset_class,
        regime=regime,
        historical_edge_bps=historical_edge_bps,
    )
    assert math.isfinite(out)
    # Weights sum to 1.0 and every term is in [-1, 1] -> score in [-1, 1].
    assert -1.0 <= out <= 1.0


# ---------------------------------------------------------------------------
# rank_strategies / margin_of
# ---------------------------------------------------------------------------


def _meta(strategy_id: str, *, asset_class: str, correlation_group_id: str) -> StrategyMetadata:
    return StrategyMetadata(
        strategy_id=strategy_id, timeframe="1D", warmup_bars=30, max_positions=1,
        gross_cap=1.0, per_symbol_cap=1.0, expected_holding_bars=10,
        asset_class=asset_class, venue="capital",
        correlation_group_id=correlation_group_id,
    )


def test_rank_strategies_sorts_best_first() -> None:
    candidates = [
        _meta("fx_breakout_basket", asset_class="fx", correlation_group_id="cfd_fx_trend"),
        _meta(
            "xau_indices_trend", asset_class="commodity",
            correlation_group_id="cfd_index_commodity_trend",
        ),
    ]
    ranked = rank_strategies(
        candidates, ticker_asset_class="commodity", regime="bull_trend",
        historical_edge_bps={},
    )
    assert ranked[0].strategy_id == "xau_indices_trend"  # asset_class match wins
    assert ranked[0].score > ranked[1].score


def test_rank_strategies_empty_input_returns_empty() -> None:
    assert rank_strategies(
        [], ticker_asset_class="fx", regime="chop", historical_edge_bps={}
    ) == []


def test_margin_of_none_when_fewer_than_two() -> None:
    single = rank_strategies(
        [_meta("a", asset_class="fx", correlation_group_id="")],
        ticker_asset_class="fx", regime="chop", historical_edge_bps={},
    )
    assert margin_of(single) is None
    assert margin_of([]) is None


def test_margin_of_is_nonnegative_gap() -> None:
    candidates = [
        _meta("fx_breakout_basket", asset_class="fx", correlation_group_id="cfd_fx_trend"),
        _meta(
            "xau_indices_trend", asset_class="commodity",
            correlation_group_id="cfd_index_commodity_trend",
        ),
    ]
    ranked = rank_strategies(
        candidates, ticker_asset_class="commodity", regime="bull_trend",
        historical_edge_bps={},
    )
    margin = margin_of(ranked)
    assert margin is not None
    assert margin >= 0.0
    assert margin > AMBIGUOUS_MARGIN_THRESHOLD  # a clear asset_class mismatch


def test_history_dict_lookup_uses_strategy_id() -> None:
    candidates = [
        _meta("a", asset_class="fx", correlation_group_id=""),
        _meta("b", asset_class="fx", correlation_group_id=""),
    ]
    ranked = rank_strategies(
        candidates, ticker_asset_class="fx", regime="crisis",
        historical_edge_bps={"b": 80.0},
    )
    assert ranked[0].strategy_id == "b"
