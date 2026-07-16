"""Source-level wiring lint — capital_macro_riskoff_catalyst SHADOW emit tagger
(P3 promotion) actually reaches the live ``_run_tick`` fan-out, sitting AFTER
mv construction and BEFORE the per-strategy dispatch loop (mirrors
``test_tsmom_literature_shadow_wiring.py``'s pattern for the sibling shadow).

DEMO/PAPER · behavior-0 · flow_not_block · 9-stack ban untouched. The strategy
itself stays ``dispatch_eligible=False`` — this wiring never gates a trade.
"""

from __future__ import annotations

from pathlib import Path

from polaris.strategies import STRATEGY_REGISTRY

_SRC = Path("polaris/scripts/_production_tick.py").read_text()


def test_log_capital_macro_riskoff_shadow_imported_and_called() -> None:
    assert "log_capital_macro_riskoff_shadow" in _SRC


def test_shadow_call_sits_after_mv_construction() -> None:
    mv_idx = _SRC.index("mv = build_real_market_view(")
    call_idx = _SRC.index("log_capital_macro_riskoff_shadow(")
    assert mv_idx < call_idx


def test_shadow_call_sits_before_strategy_loop() -> None:
    call_idx = _SRC.index("log_capital_macro_riskoff_shadow(")
    loop_idx = _SRC.index("for strategy in strategies_for_tf:")
    assert call_idx < loop_idx


def test_shadow_call_gated_to_1h_timeframe() -> None:
    call_idx = _SRC.index("log_capital_macro_riskoff_shadow(")
    preceding = _SRC[max(0, call_idx - 600) : call_idx]
    assert 'timeframe == "1H"' in preceding


def test_shadow_call_gated_by_own_bar_advance_mark() -> None:
    """Wiring pin: the call sits behind ``bar_advance_due`` keyed on
    ``last_macro_riskoff_shadow_bar_ts_by_key`` — its OWN mark, mirroring the
    tsmom shadow's dedup fix (this strategy's own
    ``evaluates_in_progress_bar=True`` bypasses the outer per-strategy dedup
    gate for the WHOLE (capital, 1H) bucket)."""
    call_idx = _SRC.index("log_capital_macro_riskoff_shadow(")
    preceding = _SRC[max(0, call_idx - 600) : call_idx]
    assert "bar_advance_due(" in preceding
    assert "last_macro_riskoff_shadow_bar_ts_by_key" in preceding


def test_shadow_call_never_feeds_a_sizing_or_gating_seam() -> None:
    """Behavior-0 structural check: the call's own statement block must not
    reference sizing_hint/strength/T4 — it only reads mv/regime/venue and
    writes to gate_shadow_events."""
    call_idx = _SRC.index("log_capital_macro_riskoff_shadow(")
    close_idx = _SRC.index("\n                    )", call_idx) + len(
        "\n                    )"
    )
    segment = _SRC[call_idx:close_idx]
    assert "sizing_hint" not in segment
    assert "strength" not in segment


def test_strategy_stays_registered_but_dispatch_ineligible() -> None:
    """Sanity: if a future change flips dispatch_eligible=True without going
    through the P0a evolve honest-N gate, this catches it (the strategy's own
    module docstring documents the required promotion path)."""
    assert "capital_macro_riskoff_catalyst" in STRATEGY_REGISTRY
    assert (
        STRATEGY_REGISTRY["capital_macro_riskoff_catalyst"].metadata.dispatch_eligible
        is False
    )
