"""Source-level wiring lints — the 3 compute-scheduling specs actually reach
the live ``_run_tick`` fan-out (registered != wired is the recurring INERT
class this project has hit before — mirrors
``test_bar_path_trade_eligible_defer.py``'s wiring-lint pattern).

DEMO/PAPER · flow_not_block · 9-stack ban untouched · compute scheduling only.
"""

from __future__ import annotations

from pathlib import Path

_SRC = Path("polaris/scripts/_production_tick.py").read_text()


# ---------------------------------------------------------------------------
# spec_a — bar-advance gate wired into the strategy dispatch fan-out.
# ---------------------------------------------------------------------------


def test_bar_advance_due_imported_and_called() -> None:
    assert "bar_advance_due" in _SRC, (
        "spec_a: the bar-advance predicate must be wired into the strategy "
        "dispatch fan-out, else the 1D/1H close-only re-eval waste persists."
    )


def test_evaluates_in_progress_bar_consulted_before_gating() -> None:
    assert "evaluates_in_progress_bar" in _SRC, (
        "spec_a: an exempt strategy (session/orderbook/funding/VIX-driven) "
        "must be readable at the dispatch site so it can bypass the gate."
    )


def test_last_eval_bar_ts_by_key_is_read_and_written() -> None:
    assert "last_eval_bar_ts_by_key" in _SRC, (
        "spec_a: the per-key last-eval ts must be threaded through state, "
        "else the gate has no memory across ticks."
    )


def test_bar_advance_gate_sits_before_generate_raw_signal_call() -> None:
    """The gate must skip BEFORE ``generate_raw_signal`` is invoked (the
    actual compute being saved), not merely log/count after the fact."""
    gate_idx = _SRC.index("bar_advance_due")
    call_idx = _SRC.index("strategy.generate_raw_signal(mv)")
    assert gate_idx < call_idx, (
        "the bar_advance_due check must precede the generate_raw_signal call "
        "it is meant to skip."
    )


def test_bar_advance_gate_reads_last_stored_bar_ts_from_md_conn() -> None:
    """storage-split (round 4 fix): bars is marketdata-domain — the gate's
    ``last_stored_bar_ts`` read must use ``_md_conn`` (the 620 sibling
    already used a few lines above for the SAME bars table), not the trading
    ``conn`` (permanently empty post-split)."""
    assert "last_stored_bar_ts(_md_conn, instrument_id, timeframe)" in _SRC
    assert "last_stored_bar_ts(conn, instrument_id, timeframe)" not in _SRC


# ---------------------------------------------------------------------------
# spec_b — session-aware entry-fanout skip wired at the NEW-ENTRY seam only.
# ---------------------------------------------------------------------------


def test_entry_fanout_active_imported_and_called() -> None:
    assert "entry_fanout_active" in _SRC, (
        "spec_b: the session-aware entry-fanout predicate must be wired into "
        "the NEW-ENTRY dispatch seam."
    )


def test_entry_fanout_active_gate_sits_before_generate_raw_signal_call() -> None:
    gate_idx = _SRC.index("entry_fanout_active")
    call_idx = _SRC.index("strategy.generate_raw_signal(mv)")
    assert gate_idx < call_idx


def test_recalc_lane_never_calls_entry_fanout_active() -> None:
    """Structural invariant: the exit/recalc lane must NOT be gated by
    entry_fanout_active — open-position management stays byte-identical
    regardless of session state. Checked by position: the predicate must not
    appear between run_recalc_for_active_positions and recalc_active_positions
    (the exit lane call sites)."""
    recalc_start = _SRC.index("await run_recalc_for_active_positions(")
    recalc_end = _SRC.index("await recalc_active_positions(")
    span_end = _SRC.index("\n", recalc_end) + 400  # small buffer past the call
    segment = _SRC[recalc_start:span_end]
    assert "entry_fanout_active" not in segment


# ---------------------------------------------------------------------------
# spec_c — idle backoff wired per-venue, ingest stays unconditional.
# ---------------------------------------------------------------------------


def test_idle_backoff_helpers_imported_and_called() -> None:
    assert "idle_backoff_next_due" in _SRC, (
        "spec_c: the idle backoff scheduling helper must be wired into the "
        "per-tick venue skip decision."
    )
    assert "idle_backoff_reset" in _SRC, (
        "spec_c: the idle-reset predicate must be consulted so activity "
        "(new bars/signals/session-open) immediately resumes 5s cadence."
    )


def test_ingest_bars_call_precedes_idle_backoff_decision() -> None:
    """Ordering pin (risk #5 in the design doc): ingest must run BEFORE the
    per-tick fanout-due DECISION is computed, else a same-tick new bar cannot
    reset the backoff until the NEXT tick (one extra tick of stale wake)."""
    ingest_idx = _SRC.index("await ingest_bars_per_timeframe(")
    decision_idx = _SRC.index("venues_fanout_due", ingest_idx)
    assert ingest_idx < decision_idx


def test_fanout_next_due_by_venue_threaded_through_state() -> None:
    assert "fanout_next_due_by_venue" in _SRC
    assert "fanout_idle_streak_by_venue" in _SRC


# ---------------------------------------------------------------------------
# Held-position carve-out — spec_b/spec_c must never suppress an OPEN position's
# regime refresh or dispatch bookkeeping (conservative, matches the exit/recalc
# lane's own unconditional treatment of held symbols).
# ---------------------------------------------------------------------------


def test_open_position_symbols_exempts_session_and_idle_skip() -> None:
    assert "open_position_symbols" in _SRC, (
        "an OPEN position must be exempt from both the entry_fanout_active "
        "session skip and the idle-backoff fanout skip."
    )


def test_prefetch_pregate_precedes_bar_read() -> None:
    """spec_a's prefetch pre-gate (skip the 240-row read/build_real_market_view/
    technicals-write entirely for an all-close-only bucket with no bar advance)
    must sit BEFORE ``read_recent_bars_ondemand`` — else the compute it is
    meant to save (the fetch + indicator build) already ran."""
    gate_idx = _SRC.index("bucket_has_exempt")
    read_idx = _SRC.index("bars = await read_recent_bars_ondemand(")
    assert gate_idx < read_idx


def test_bar_gate_keys_include_strategy_id_not_just_venue_symbol_timeframe() -> None:
    """BLOCKER regression: a 3-tuple ``(venue, symbol, timeframe)`` key lets
    the FIRST close-only strategy sharing a bucket permanently starve every
    sibling (their generate_raw_signal is never called again). Both the
    per-strategy gate AND the bucket-level prefetch pre-gate must key on a
    4-tuple that includes strategy_id."""
    strategy_key_idx = _SRC.index("strategy_key = (venue, symbol, timeframe")
    strategy_key_line = _SRC[strategy_key_idx : _SRC.index("\n", strategy_key_idx)]
    assert "strategy_id" in strategy_key_line, (
        "the per-strategy bar-advance gate key must include strategy_id "
        "(4-tuple), else siblings sharing a bucket starve each other"
    )

    # The bucket-level prefetch pre-gate must not use a bare 3-tuple key
    # either — it must derive its due-ness from the SAME per-strategy marks
    # (checked across ALL bucket strategies), never a single shared key that
    # the per-strategy loop's writes could clobber.
    pregate_start = _SRC.index("bucket_has_exempt = any(")
    read_idx = _SRC.index("bars = await read_recent_bars_ondemand(")
    pregate_segment = _SRC[pregate_start:read_idx]
    assert "bucket_key = (venue, symbol, timeframe)" not in pregate_segment, (
        "the bucket prefetch pre-gate must not read/write a shared 3-tuple "
        "key that a per-strategy write can clobber — it must check ALL "
        "close-only siblings' own (4-tuple) marks before skipping the fetch"
    )
    assert "strategy.metadata.strategy_id" in pregate_segment or (
        "s.metadata.strategy_id" in pregate_segment
    ), (
        "the bucket pre-gate must consult each bucket strategy's own "
        "strategy_id-keyed mark, not a bucket-only key"
    )
