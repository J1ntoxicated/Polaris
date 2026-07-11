"""Source-level wiring lint — the TSMOM literature shadow tagger (frontgate
item #2) actually reaches the live ``_run_tick`` fan-out, sitting AFTER mv
construction and BEFORE the per-strategy dispatch loop (mirrors
``test_run_tick_compute_scheduling_wiring.py``'s pattern).

DEMO/PAPER · behavior-0 · flow_not_block · 9-stack ban untouched.
"""

from __future__ import annotations

from pathlib import Path

_SRC = Path("polaris/scripts/_production_tick.py").read_text()


def test_log_tsmom_literature_shadow_imported_and_called() -> None:
    assert "log_tsmom_literature_shadow" in _SRC


def test_shadow_call_sits_after_mv_construction() -> None:
    mv_idx = _SRC.index("mv = build_real_market_view(")
    call_idx = _SRC.index("log_tsmom_literature_shadow(")
    assert mv_idx < call_idx


def test_shadow_call_sits_before_strategy_loop() -> None:
    call_idx = _SRC.index("log_tsmom_literature_shadow(")
    loop_idx = _SRC.index("for strategy in strategies_for_tf:")
    assert call_idx < loop_idx


def test_shadow_call_gated_to_1d_timeframe() -> None:
    """The literature-fixed 12-1 calc is a 1D-bar concept only — the wiring
    must not fire it for intraday timeframes."""
    call_idx = _SRC.index("log_tsmom_literature_shadow(")
    preceding = _SRC[max(0, call_idx - 200) : call_idx]
    assert 'timeframe == "1D"' in preceding


def test_shadow_call_never_feeds_a_sizing_or_gating_seam() -> None:
    """Behavior-0 structural check: the call's own statement block must not
    reference sizing_hint/strength/T4 — it only reads bars/regime/venue and
    writes to gate_shadow_events."""
    call_idx = _SRC.index("log_tsmom_literature_shadow(")
    # capture through the closing paren of the call (multi-line call site)
    close_idx = _SRC.index("\n                )", call_idx) + len(
        "\n                )"
    )
    segment = _SRC[call_idx:close_idx]
    assert "sizing_hint" not in segment
    assert "strength" not in segment
