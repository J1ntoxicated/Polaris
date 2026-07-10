"""§0a — Equity cluster-cap (Alpaca sleeve, hard dependency #1).

DEMO/PAPER virtual (no real fees). Aggressive bias preserved: the two new
equity cluster caps (``equity:beta_trend`` / ``equity:meanrev``) are generous
DEMO defaults (0.99, matching the existing crypto/FX/XAU cluster-cap
convention) — this is a routing/attribution addition, NOT a defensive
throttle (no multiplier added to the T4 base×continuous×tier×cell chain).

POLICY under test:
- ``resolve_cluster_id`` gains a ``strategy_id: str = ""`` kwarg. Default ""
  keeps every EXISTING (non-equity) call site byte-identical — the new
  ``asset_class == "equity"`` branch is additive only.
- ``asset_class == "equity"``: strategy_id in {"equity_bb_meanrev_15m",
  "connors_rsi2"} -> "equity:meanrev"; every other equity strategy_id ->
  "equity:beta_trend". Never None for equity (unlike the old un-clustered
  behaviour) — every equity strategy now attributes to ONE of the two sleeves.
- Non-equity asset classes are COMPLETELY UNCHANGED (byte-identical) —
  crypto/FX/XAU-indices routing must not regress.
"""

from __future__ import annotations

import os

import pytest

from polaris.core.sizing.cluster_cap import (
    CLUSTER_DEFINITIONS,
    cluster_remaining_pct,
    resolve_cluster_definitions,
    resolve_cluster_id,
)
from polaris.core.sizing.schema import (
    CLUSTER_EQUITY_BETA_TREND_PCT,
    CLUSTER_EQUITY_MEANREV_PCT,
    PositionRiskState,
    cluster_equity_beta_trend_pct,
    cluster_equity_meanrev_pct,
)

# ---------------------------------------------------------------------------
# A — schema constants + env-override accessors (0.99 DEMO convention)
# ---------------------------------------------------------------------------


def test_equity_cluster_pct_defaults_match_demo_convention() -> None:
    assert CLUSTER_EQUITY_BETA_TREND_PCT == 0.99
    assert CLUSTER_EQUITY_MEANREV_PCT == 0.99


def test_equity_cluster_pct_accessors_return_default_when_env_unset() -> None:
    os.environ.pop("POLARIS_CAP_CLUSTER_EQUITY_BETA_TREND_PCT", None)
    os.environ.pop("POLARIS_CAP_CLUSTER_EQUITY_MEANREV_PCT", None)
    assert cluster_equity_beta_trend_pct() == CLUSTER_EQUITY_BETA_TREND_PCT
    assert cluster_equity_meanrev_pct() == CLUSTER_EQUITY_MEANREV_PCT


def test_equity_cluster_pct_accessors_read_env_override() -> None:
    os.environ["POLARIS_CAP_CLUSTER_EQUITY_BETA_TREND_PCT"] = "0.55"
    os.environ["POLARIS_CAP_CLUSTER_EQUITY_MEANREV_PCT"] = "0.35"
    try:
        assert cluster_equity_beta_trend_pct() == 0.55
        assert cluster_equity_meanrev_pct() == 0.35
    finally:
        del os.environ["POLARIS_CAP_CLUSTER_EQUITY_BETA_TREND_PCT"]
        del os.environ["POLARIS_CAP_CLUSTER_EQUITY_MEANREV_PCT"]


def test_equity_cluster_pct_accessor_falls_back_on_bad_env() -> None:
    os.environ["POLARIS_CAP_CLUSTER_EQUITY_BETA_TREND_PCT"] = "not-a-float"
    try:
        assert cluster_equity_beta_trend_pct() == CLUSTER_EQUITY_BETA_TREND_PCT
    finally:
        del os.environ["POLARIS_CAP_CLUSTER_EQUITY_BETA_TREND_PCT"]


# ---------------------------------------------------------------------------
# B — CLUSTER_DEFINITIONS / resolve_cluster_definitions() include equity
# ---------------------------------------------------------------------------


def test_cluster_definitions_include_equity_sleeves() -> None:
    assert CLUSTER_DEFINITIONS["equity:beta_trend"] == CLUSTER_EQUITY_BETA_TREND_PCT
    assert CLUSTER_DEFINITIONS["equity:meanrev"] == CLUSTER_EQUITY_MEANREV_PCT


def test_resolve_cluster_definitions_include_equity_sleeves() -> None:
    defs = resolve_cluster_definitions()
    assert defs["equity:beta_trend"] == cluster_equity_beta_trend_pct()
    assert defs["equity:meanrev"] == cluster_equity_meanrev_pct()


def test_cluster_definitions_still_has_prior_three() -> None:
    # Additive only — the crypto/XAU/FX clusters must survive unchanged.
    for cid in ("crypto:BTC+ETH", "cfd:XAU+INDICES", "cfd:FX_MAJORS"):
        assert cid in CLUSTER_DEFINITIONS


# ---------------------------------------------------------------------------
# C — resolve_cluster_id equity routing (strategy_id kwarg)
# ---------------------------------------------------------------------------


def test_connors_rsi2_routes_to_meanrev_cluster() -> None:
    cid = resolve_cluster_id(
        underlying_group_id="equity:AAPL", asset_class="equity",
        symbol="AAPL", strategy_id="connors_rsi2",
    )
    assert cid == "equity:meanrev"


def test_equity_bb_meanrev_15m_routes_to_meanrev_cluster() -> None:
    cid = resolve_cluster_id(
        underlying_group_id="equity:SPY", asset_class="equity",
        symbol="SPY", strategy_id="equity_bb_meanrev_15m",
    )
    assert cid == "equity:meanrev"


def test_other_equity_strategy_routes_to_beta_trend_cluster() -> None:
    cid = resolve_cluster_id(
        underlying_group_id="equity:AAPL", asset_class="equity",
        symbol="AAPL", strategy_id="equity_donchian55_breakout",
    )
    assert cid == "equity:beta_trend"


def test_equity_with_no_strategy_id_defaults_to_beta_trend() -> None:
    # Unknown/blank strategy_id on an equity signal must still resolve to a
    # sleeve (never None) — beta_trend is the non-meanrev default bucket.
    cid = resolve_cluster_id(
        underlying_group_id="equity:AAPL", asset_class="equity", symbol="AAPL",
    )
    assert cid == "equity:beta_trend"


# ---------------------------------------------------------------------------
# D — byte-identical guard: non-equity asset classes untouched by the new
#     strategy_id kwarg / equity branch.
# ---------------------------------------------------------------------------


def test_crypto_btc_eth_unaffected_by_strategy_id() -> None:
    cid = resolve_cluster_id(
        underlying_group_id="crypto:BTC", asset_class="crypto",
        symbol="BTC-USDT", strategy_id="connors_rsi2",
    )
    assert cid == "crypto:BTC+ETH"


def test_fx_majors_unaffected_by_strategy_id() -> None:
    cid = resolve_cluster_id(
        underlying_group_id="forex:EURUSD", asset_class="fx",
        symbol="EURUSD", strategy_id="equity_bb_meanrev_15m",
    )
    assert cid == "cfd:FX_MAJORS"


def test_xau_indices_unaffected_by_strategy_id() -> None:
    cid = resolve_cluster_id(
        underlying_group_id="commodity:XAU", asset_class="metal", symbol="XAUUSD",
    )
    assert cid == "cfd:XAU+INDICES"


def test_unmapped_non_equity_still_returns_none() -> None:
    cid = resolve_cluster_id(
        underlying_group_id="forex:USDSGD", asset_class="fx", symbol="USDSGD",
    )
    assert cid is None


def test_resolve_cluster_id_default_kwarg_is_source_compatible() -> None:
    # Every pre-existing call site omits strategy_id entirely — the kwarg must
    # have a default so no caller needs updating.
    import inspect

    sig = inspect.signature(resolve_cluster_id)
    assert sig.parameters["strategy_id"].default == ""


# ---------------------------------------------------------------------------
# E — cluster_remaining_pct wiring for the new equity sleeves (headroom math)
# ---------------------------------------------------------------------------


def test_equity_meanrev_cluster_headroom_sums_only_matching_cluster() -> None:
    os.environ.pop("POLARIS_CAP_CLUSTER_EQUITY_MEANREV_PCT", None)
    open_positions = [
        PositionRiskState(
            venue="alpaca", symbol="AAPL", instrument_id="alpaca:AAPL",
            underlying_group_id="equity:AAPL", cluster_id="equity:meanrev",
            strategy="connors_rsi2", track="C", signal_strength=1.0,
            open_risk_pct=0.10, notional_usd=1000.0, opened_ts=1,
        ),
        PositionRiskState(
            venue="alpaca", symbol="MSFT", instrument_id="alpaca:MSFT",
            underlying_group_id="equity:MSFT", cluster_id="equity:beta_trend",
            strategy="equity_donchian55_breakout", track="C",
            signal_strength=1.0, open_risk_pct=0.50, notional_usd=1000.0,
            opened_ts=1,
        ),
    ]
    rem = cluster_remaining_pct(cluster_id="equity:meanrev", open_positions=open_positions)
    assert rem is not None
    assert rem == pytest.approx(CLUSTER_EQUITY_MEANREV_PCT - 0.10)


# ---------------------------------------------------------------------------
# F — engine.py wiring: the T4 sizing engine passes strategy_id through so an
#     equity SignalIntent actually resolves a cluster (not silently None).
# ---------------------------------------------------------------------------


def test_engine_call_site_passes_strategy_id_source_lint() -> None:
    from pathlib import Path

    src = Path("polaris/core/sizing/engine.py").read_text()
    assert "strategy_id=intent.strategy" in src, (
        "engine.py resolve_cluster_id call must pass strategy_id=intent.strategy "
        "so equity signals route to the correct cluster (§0a hard dependency #1)."
    )
