"""TDD — delegation-gate strategy profile derivation (P2b, SHADOW v1).

Design SSOT: vault/50_research/delegation-gate-blueprint.md. edge_type_for
must classify EVERY live registered strategy (no "unknown" drift as new
strategies are added) and asset_class_match must directly catch the audited
routing bug (declared vs observed asset_class mismatch).
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Generator

import pytest
from hypothesis import given
from hypothesis import strategies as st

from polaris.core.delegation.strategy_profile import asset_class_match, edge_type_for
from polaris.strategies.base import BaseStrategy

# ---------------------------------------------------------------------------
# edge_type_for — full live-registry coverage (no strategy left "unknown")
# ---------------------------------------------------------------------------

_ENV = "POLARIS_VIRTUAL_ACCOUNT"


@pytest.fixture
def _virtual_strategy_registry() -> Generator[dict[str, type[BaseStrategy]]]:
    """The RUNTIME-DEFAULT registry (CLAUDE.md "VIRTUAL ACCOUNT 기본 ON" —
    ``POLARIS_VIRTUAL_ACCOUNT=1``), a strict superset of the REAL-only
    registry (virtual mode only ADDS ids, per
    ``polaris/strategies/__init__.py``'s ``if virtual_mode_enabled(): ...``
    block — it never removes any). ``STRATEGY_REGISTRY`` is a module-level
    dict built once from the env var at import time, so only
    ``importlib.reload`` observes the virtual-mode membership — mirrors
    ``tests/test_virtual_equity_readmit.py``'s ``_reload_with_env`` pattern.
    Asserting non-"unknown" over this superset covers the REAL registry too.
    """
    prior = os.environ.get(_ENV)
    os.environ[_ENV] = "1"
    import polaris.strategies as strategies_mod

    strategies_mod = importlib.reload(strategies_mod)
    yield strategies_mod.STRATEGY_REGISTRY
    if prior is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = prior
    importlib.reload(strategies_mod)


def test_every_registered_strategy_classifies_non_unknown(
    _virtual_strategy_registry: dict[str, type[BaseStrategy]],
) -> None:
    for strategy_id, cls in _virtual_strategy_registry.items():
        meta = cls.metadata
        edge = edge_type_for(
            strategy_id=strategy_id, correlation_group_id=meta.correlation_group_id
        )
        assert edge != "unknown", f"{strategy_id} classified unknown"


def test_trend_continuation_pullback_entries_classify_trend_not_reversion() -> None:
    # Regression: a trend-CONTINUATION strategy whose entry trigger is a
    # "pullback" must not misread as mean-reversion — TREND is checked before
    # REVERSION precisely for this case.
    assert (
        edge_type_for(
            strategy_id="macd_ema_trend_pullback",
            correlation_group_id="macd_ema_trend_continuation",
        )
        == "trend"
    )
    assert (
        edge_type_for(
            strategy_id="equity_etf_trend_pullback",
            correlation_group_id="equity_etf_trend_continuation",
        )
        == "trend"
    )


def test_mean_reversion_strategies_classify_reversion() -> None:
    assert edge_type_for(
        strategy_id="rsi_bb_pullback", correlation_group_id="spot_mean_reversion"
    ) == "reversion"
    assert edge_type_for(
        strategy_id="cci_reversion", correlation_group_id="cfd_commodity_reversion"
    ) == "reversion"
    assert edge_type_for(
        strategy_id="connors_rsi2", correlation_group_id="equity_connors_reversion"
    ) == "reversion"


def test_breakout_strategies_classify_breakout() -> None:
    assert edge_type_for(
        strategy_id="okx_donchian_55_breakout",
        correlation_group_id="okx_donchian_55_breakout",
    ) == "breakout"
    assert edge_type_for(
        strategy_id="gold_breakout_1h", correlation_group_id="cfd_gold_breakout_1h"
    ) == "breakout"


def test_unknown_strategy_id_classifies_unknown() -> None:
    assert edge_type_for(strategy_id="mystery_alpha", correlation_group_id="") == (
        "unknown"
    )


@given(
    strategy_id=st.text(min_size=0, max_size=40),
    correlation_group_id=st.text(min_size=0, max_size=40),
)
def test_property_edge_type_total_function(
    strategy_id: str, correlation_group_id: str
) -> None:
    # Never raises, always returns one of the 4 literal values.
    out = edge_type_for(
        strategy_id=strategy_id, correlation_group_id=correlation_group_id
    )
    assert out in ("trend", "reversion", "breakout", "unknown")


# ---------------------------------------------------------------------------
# asset_class_match
# ---------------------------------------------------------------------------


def test_exact_match_is_positive() -> None:
    assert asset_class_match("commodity", "commodity") == 1.0


def test_mismatch_is_negative() -> None:
    # The audited P2 bug: cci_reversion declares "commodity" but dispatched on
    # an fx-classified ticker.
    assert asset_class_match("commodity", "fx") == -1.0


def test_case_insensitive_match() -> None:
    assert asset_class_match("Commodity", "commodity") == 1.0


def test_index_indices_alias_matches() -> None:
    # Pre-existing metadata naming split (index vs indices for the same
    # Capital instrument family) — normalized so these read as a match.
    assert asset_class_match("index", "indices") == 1.0
    assert asset_class_match("indices", "index") == 1.0


def test_empty_either_side_is_neutral() -> None:
    assert asset_class_match("", "fx") == 0.0
    assert asset_class_match("fx", "") == 0.0
    assert asset_class_match("", "") == 0.0


@given(a=st.text(max_size=20), b=st.text(max_size=20))
def test_property_asset_class_match_bounded(a: str, b: str) -> None:
    assert asset_class_match(a, b) in (-1.0, 0.0, 1.0)
