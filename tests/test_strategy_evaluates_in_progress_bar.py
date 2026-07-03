"""``StrategyMetadata.evaluates_in_progress_bar`` — bar-advance gate exemption flag.

DEMO/PAPER · flow_not_block. Default False = "pure function of closed bars,
safe to skip a same-bar re-eval" (the 17 close-only dispatch-eligible
strategies). True = exempt (this strategy's signal depends on something that
can change BETWEEN bar closes — session clock / live orderbook / funding /
intraday altdata) — it must keep re-evaluating every tick, byte-identical to
pre-gate behaviour. Exactly the 5 strategies audited in the design doc.
"""

from __future__ import annotations

from polaris.strategies import STRATEGY_REGISTRY

# The FIVE strategies whose generate_raw_signal reads a bars-EXTERNAL input
# that can change between bar closes:
#   - session_breakout: MarketView.is_session_open_window (wall-clock minute
#     window, session_window_now(now_ts) — changes intra-bar).
#   - weekend_thin_book_flush_maker / weekend_funding_capitulation_maker: the
#     weekend-only edge depends on live orderbook depth (fill path) / funding
#     rate (AltDataView, refreshed on its own cadence independent of the 1H bar
#     close).
#   - gold_riskoff_trend_amplify: _riskoff_scalar reads MarketView.altdata.vix
#     (intraday VIX cadence, independent of the 1D bar close).
#   - volume_burst: 1m intra-minute volume z-score (KILLed/unregistered —
#     module kept read-only per the strategies/__init__.py history; the flag
#     is still set on the module for documentation completeness, but it has
#     zero live dispatch effect since it is absent from STRATEGY_REGISTRY).
_EXPECTED_EXEMPT_REGISTERED = {
    "session_breakout",
    "weekend_thin_book_flush_maker",
    "weekend_funding_capitulation_maker",
    "gold_riskoff_trend_amplify",
}


def test_default_is_false_for_close_only_strategies() -> None:
    """A strategy that does not opt in stays gate-eligible (default False) —
    byte-identical semantics to before this field existed."""
    from polaris.strategies.okx_donchian_55_breakout import (
        OKXDonchian55BreakoutStrategy,
    )

    assert OKXDonchian55BreakoutStrategy.metadata.evaluates_in_progress_bar is False


def test_exactly_the_four_registered_strategies_are_exempt() -> None:
    """The bar-advance gate must exempt exactly the registered strategies whose
    signal depends on a bars-external input — no more, no less. A stray True
    on a close-only strategy would silently disable its gate savings; a
    missing True on a genuinely intra-bar strategy would delay its trigger by
    up to one bar (the #1 risk in the design doc)."""
    actual_exempt = {
        strategy_id
        for strategy_id, cls in STRATEGY_REGISTRY.items()
        if cls.metadata.evaluates_in_progress_bar
    }
    assert actual_exempt == _EXPECTED_EXEMPT_REGISTERED


def test_every_other_dispatch_eligible_strategy_is_gate_eligible() -> None:
    """Every dispatch-eligible strategy NOT in the exempt set is
    gate-ELIGIBLE (evaluates_in_progress_bar is False) — the dominant 1D/1H
    close-only population this whole spec targets."""
    dispatch_eligible = {
        strategy_id: cls
        for strategy_id, cls in STRATEGY_REGISTRY.items()
        if cls.metadata.dispatch_eligible
    }
    gate_eligible = {
        strategy_id
        for strategy_id, cls in dispatch_eligible.items()
        if not cls.metadata.evaluates_in_progress_bar
    }
    assert gate_eligible == set(dispatch_eligible) - _EXPECTED_EXEMPT_REGISTERED
    # Sanity: this population must be non-trivial (the actual savings target).
    assert len(gate_eligible) >= 10
