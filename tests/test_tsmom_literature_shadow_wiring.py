"""Source-level wiring lint — the TSMOM literature shadow tagger (frontgate
item #2) actually reaches the live ``_run_tick`` fan-out, sitting AFTER mv
construction and BEFORE the per-strategy dispatch loop (mirrors
``test_run_tick_compute_scheduling_wiring.py``'s pattern).

DEMO/PAPER · behavior-0 · flow_not_block · 9-stack ban untouched.
"""

from __future__ import annotations

from pathlib import Path

from polaris.scripts._production_bar_gate import bar_advance_due
from polaris.scripts._production_state import ProdLoopState
from polaris.strategies import STRATEGY_REGISTRY

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
    must not fire it for intraday timeframes. Window widened (200->550) to
    cover the per-(venue,symbol) bar-advance dedup mark now sitting between
    the ``timeframe == "1D"`` guard and the call (frontgate #2 duplicate-row
    fix — see ``last_tsmom_shadow_bar_ts_by_key``)."""
    call_idx = _SRC.index("log_tsmom_literature_shadow(")
    preceding = _SRC[max(0, call_idx - 550) : call_idx]
    assert 'timeframe == "1D"' in preceding


def test_shadow_call_never_feeds_a_sizing_or_gating_seam() -> None:
    """Behavior-0 structural check: the call's own statement block must not
    reference sizing_hint/strength/T4 — it only reads bars/regime/venue and
    writes to gate_shadow_events."""
    call_idx = _SRC.index("log_tsmom_literature_shadow(")
    # capture through the closing paren of the call (multi-line call site)
    close_idx = _SRC.index("\n                    )", call_idx) + len(
        "\n                    )"
    )
    segment = _SRC[call_idx:close_idx]
    assert "sizing_hint" not in segment
    assert "strength" not in segment


def test_shadow_call_gated_by_own_bar_advance_mark() -> None:
    """Wiring pin: the call sits behind ``bar_advance_due`` keyed on
    ``last_tsmom_shadow_bar_ts_by_key`` — its OWN mark, separate from the
    per-strategy dispatch gate above (which is skipped entirely whenever the
    bucket has an ``evaluates_in_progress_bar`` sibling, e.g. capital/1D)."""
    call_idx = _SRC.index("log_tsmom_literature_shadow(")
    preceding = _SRC[max(0, call_idx - 550) : call_idx]
    assert "bar_advance_due(" in preceding
    assert "last_tsmom_shadow_bar_ts_by_key" in preceding


# ---------------------------------------------------------------------------
# Behavioral proof — the duplicate-row bug this gate fixes (frontgate #2).
# ---------------------------------------------------------------------------

# gold_riskoff_trend_amplify (evaluates_in_progress_bar=True) shares the REAL
# (capital, 1D) dispatch bucket with close-only siblings today — its presence
# makes the OUTER per-strategy bar-advance gate a bucket-wide no-op (see
# ``bucket_has_exempt`` in ``_production_tick.py``), which is exactly the
# condition that let the tsmom shadow write re-fire every 5s tick pre-fix.
_EXEMPT_ID = "gold_riskoff_trend_amplify"


def test_capital_1d_bucket_still_has_an_exempt_sibling() -> None:
    """Sanity: if a future registry change drops this, the scenario below
    stops exercising the real production shape and must be revisited."""
    exempt = STRATEGY_REGISTRY[_EXEMPT_ID]
    assert exempt.metadata.venue == "capital"
    assert exempt.metadata.timeframe == "1D"
    assert exempt.metadata.evaluates_in_progress_bar is True


def test_tsmom_shadow_mark_fires_once_per_bar_even_when_bucket_has_exempt_sibling() -> None:
    """Reproduces the frontgate #2 duplicate-row bug and proves the fix: an
    UNCHANGED 1D bar polled across many ticks (the real shape on a capital/1D
    bucket, since ``gold_riskoff_trend_amplify``'s presence bypasses the outer
    per-strategy dedup gate for the WHOLE bucket) must log the tsmom shadow
    row exactly ONCE per distinct bar ts, using the SAME ``bar_advance_due``
    primitive + key shape (``(venue, symbol)``) the real call site uses via
    ``ProdLoopState.last_tsmom_shadow_bar_ts_by_key``."""
    state = ProdLoopState()
    venue, symbol = "capital", "XAUUSD"
    key = (venue, symbol)

    # 5 ticks on the SAME 1D bar (the exempt sibling would fire on every one
    # of these at the real call site — that's the bug's precondition).
    unchanged_ts = 86_400 * 20
    fired = []
    for _ in range(5):
        due = bar_advance_due(
            last_eval_ts=state.last_tsmom_shadow_bar_ts_by_key.get(key),
            latest_bar_ts=unchanged_ts,
        )
        if due:
            state.last_tsmom_shadow_bar_ts_by_key[key] = unchanged_ts
        fired.append(due)
    assert fired == [True, False, False, False, False], (
        "an unchanged 1D bar must log the tsmom shadow row on its first "
        "observation only — every subsequent same-bar tick (the exempt "
        "sibling's every-tick re-poll) must be suppressed, not re-inserted"
    )

    # A genuine bar advance (real new 1D close) re-fires exactly once — no
    # signal is permanently lost, only the redundant same-bar repeats.
    advanced_ts = 86_400 * 21
    due_next = bar_advance_due(
        last_eval_ts=state.last_tsmom_shadow_bar_ts_by_key.get(key),
        latest_bar_ts=advanced_ts,
    )
    assert due_next is True
