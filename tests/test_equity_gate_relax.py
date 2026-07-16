"""Equity gate relax (2026-06-27) — daily-equity strategies fire on IEX (yfinance
signal) + a small shadow validation cap bounds the unvalidated bleed.

DEMO/PAPER paper-trading. Two decisions, both flow_not_block:

A. SIP-gate RELAX — ``equity_vol_expansion_pocket_pivot`` /
   ``equity_52wk_high_breakout`` are DAILY (1D bar-close) strategies. Their signal
   data is yfinance daily bars (#21 PRIMARY — free, full US market), NOT the
   Alpaca realtime SIP/IEX tick feed. The original SIP-gate conflated "realtime
   feed entitlement" with "signal data quality"; the data-correctness premise is
   already satisfied by the feed-agnostic yfinance daily bars, so the gate no
   longer holds. The strategies now fire on ``iex`` (or any feed). The gate is
   RELAXED to a no-op, NOT a defensive block — flow is ACTIVATED.

B. shadow validation cap — Alpaca equity is commission-free (real fees 0) so the
   live demo P&L is the clean true edge. The two equity strategies are UNVALIDATED
   live (the prior -$104.58 may be overfit). A small per-strategy ``min()``
   containment (``POLARIS_EQUITY_SHADOW_CAP_PCT``) bounds each entry's notional so
   a repeat bleed stays negligible while the edge is verified live. This is prudent
   risk-sizing of an unvalidated NEW strategy (an allocation cap), NOT a defensive
   dampen of an edge strategy: it is a pure ``min()`` term in the same
   ``headroom_min`` slot as ``SINGLE_TRADE_ABSOLUTE_CEILING_PCT`` — it adds NO
   multiplier to the T4 chain (9-stack / tier / cell / -1.0R rail untouched). Jin
   lifts the cap (env unset / 0) to full size once the edge clears.
"""

from __future__ import annotations

from polaris.core.sizing.engine import (
    EQUITY_SHADOW_CAP_STRATEGIES,
    equity_shadow_validation_cap,
)
from polaris.core.sizing.schema import (
    EQUITY_SHADOW_CAP_DEFAULT_PCT,
    SINGLE_TRADE_ABSOLUTE_CEILING_PCT,
    equity_shadow_cap_pct,
)
from polaris.core.universe.schema import (
    mark_alpaca_feed_downgraded,
    reset_alpaca_runtime_feed,
)
from polaris.scripts._production_tick import (
    _all_strategies,
    equity_entry_inert_for_feed,
)
from polaris.strategies import STRATEGY_REGISTRY
from polaris.strategies.equity_52wk_high_breakout import Equity52WkHighBreakoutStrategy
from polaris.strategies.equity_vol_expansion_pocket_pivot import (
    EquityVolExpansionPocketPivotStrategy,
)
from polaris.strategies.okx_donchian_55_breakout import OKXDonchian55BreakoutStrategy
from polaris.strategies.rsi_bb_pullback import RSIBBPullbackStrategy


def _equity_strats() -> list[object]:
    return [
        EquityVolExpansionPocketPivotStrategy(),
        Equity52WkHighBreakoutStrategy(),
    ]


# ---------------------------------------------------------------------------
# A. SIP-gate RELAX — daily-equity strategies fire on IEX (yfinance signal)
# ---------------------------------------------------------------------------


def test_equity_fires_on_iex_feed() -> None:
    # The RELAX: a runtime SIP->IEX downgrade no longer makes the daily-equity
    # strategies inert — their signal is yfinance daily bars, feed-agnostic. They
    # FIRE on iex (gate is a no-op). flow_not_block: flow activated.
    reset_alpaca_runtime_feed()
    mark_alpaca_feed_downgraded()  # active feed is now iex
    try:
        for s in _equity_strats():
            assert equity_entry_inert_for_feed(s) is False
    finally:
        reset_alpaca_runtime_feed()


def test_equity_fires_on_sip_feed() -> None:
    # On sip (configured default, no downgrade) they also fire — unchanged.
    reset_alpaca_runtime_feed()
    for s in _equity_strats():
        assert equity_entry_inert_for_feed(s) is False


def test_non_equity_never_gated() -> None:
    # Non-equity (OKX / Capital) strategies were never gated and still aren't —
    # A/B byte-identical, regardless of the Alpaca feed.
    reset_alpaca_runtime_feed()
    mark_alpaca_feed_downgraded()  # iex
    try:
        assert equity_entry_inert_for_feed(RSIBBPullbackStrategy()) is False
        assert equity_entry_inert_for_feed(OKXDonchian55BreakoutStrategy()) is False
    finally:
        reset_alpaca_runtime_feed()


def test_equity_strategies_still_registered_and_dispatched() -> None:
    # SUPERSEDED by the B1 prune (2026-07-06) — the relax itself did not touch
    # registration/dispatch, but the subsequent live-ledger forensic
    # (-$137.23 / -$431.05, 0% win) KILLed both at dispatch (100.9% of the
    # -$2,024 book loss). Neither dispatches under REAL/default env.
    #
    # P3 promotion (2026-07-16): equity_52wk_high_breakout formalized off the
    # ad hoc VIRTUAL-only registry path — unconditionally registered now,
    # dispatch_eligible=False under REAL carries the KILL.
    # equity_vol_expansion_pocket_pivot is out of that promotion's scope and
    # stays fully un-registered (unchanged).
    assert "equity_vol_expansion_pocket_pivot" not in STRATEGY_REGISTRY
    assert "equity_52wk_high_breakout" in STRATEGY_REGISTRY
    assert (
        STRATEGY_REGISTRY["equity_52wk_high_breakout"].metadata.dispatch_eligible
        is False
    )
    dispatch_ids = {s.metadata.strategy_id for s in _all_strategies()}
    assert "equity_vol_expansion_pocket_pivot" not in dispatch_ids
    assert "equity_52wk_high_breakout" not in dispatch_ids


# ---------------------------------------------------------------------------
# B. shadow validation cap — small min() containment, unvalidated equity only
# ---------------------------------------------------------------------------


def test_shadow_cap_applies_to_equity_strategies() -> None:
    # The two unvalidated equity strategies get the small shadow validation cap.
    for sid in ("equity_vol_expansion_pocket_pivot", "equity_52wk_high_breakout"):
        cap = equity_shadow_validation_cap(sid)
        assert cap == EQUITY_SHADOW_CAP_DEFAULT_PCT
        # The shadow cap is SMALL — strictly below the absolute single-trade
        # ceiling, so it actually binds (bounds the unvalidated bleed).
        assert cap < SINGLE_TRADE_ABSOLUTE_CEILING_PCT


def test_shadow_cap_noop_for_other_strategies() -> None:
    # Every non-equity-validation strategy gets the absolute ceiling (no-op): the
    # cap never narrows the headroom for OKX / Capital / validated strategies.
    for sid in ("rsi_bb_pullback", "okx_donchian_55_breakout", "xau_indices_trend"):
        assert equity_shadow_validation_cap(sid) == SINGLE_TRADE_ABSOLUTE_CEILING_PCT


def test_shadow_cap_strategy_set_is_exactly_the_two_equity() -> None:
    # The shadow-cap membership is explicitly the two equity validation strategies
    # — no accidental over-reach onto edge strategies (this is allocation sizing of
    # the unvalidated new strategies, NOT an edge dampen).
    assert frozenset(
        {"equity_vol_expansion_pocket_pivot", "equity_52wk_high_breakout"}
    ) == EQUITY_SHADOW_CAP_STRATEGIES


def test_shadow_cap_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Operator (Jin) raises the cap toward full size post-validation via the env.
    monkeypatch.setenv("POLARIS_EQUITY_SHADOW_CAP_PCT", "0.05")
    assert equity_shadow_cap_pct() == 0.05
    assert equity_shadow_validation_cap("equity_52wk_high_breakout") == 0.05


def test_shadow_cap_env_unset_is_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("POLARIS_EQUITY_SHADOW_CAP_PCT", raising=False)
    assert equity_shadow_cap_pct() == EQUITY_SHADOW_CAP_DEFAULT_PCT


def test_shadow_cap_invalid_env_falls_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("POLARIS_EQUITY_SHADOW_CAP_PCT", "not-a-number")
    assert equity_shadow_cap_pct() == EQUITY_SHADOW_CAP_DEFAULT_PCT
