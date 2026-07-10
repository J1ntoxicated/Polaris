"""TDD — _run_tick discrete-stage exception isolation (forensic wf_1f586d0a
tick-body fix #3).

Before this fix, ``production_paper_loop.py``'s tick loop wrapped the WHOLE
``_run_tick`` call in ONE try/except — a fault ANYWHERE inside it (100/100
sampled tick-aborts were ``sqlite3.OperationalError: database is locked``)
discarded every OTHER stage's work for that tick too (ingest already ran, but
recalc/regime/dispatch/rotation never got a chance). These tests pin:

  1. ``_log_tick_stage_fault`` (the new stage-fault logger) actually logs the
     STAGE NAME + exception TYPE + message — not a bare ``%r`` repr (the
     pre-fix outer catch-all's only diagnostic, which could not distinguish
     which of dozens of possible statements inside a monolithic try failed).
  2. Each discrete stage call inside ``_run_tick`` is wrapped in its OWN
     try/except (source-position lint — mirrors this codebase's established
     pattern for ``_run_tick`` internals, e.g.
     ``test_run_tick_compute_scheduling_wiring.py`` /
     ``test_opposing_entry_wire.py``, since a full live invocation of
     ``_run_tick`` requires the whole production dependency graph).

DEMO/PAPER only — fault-isolation/observability change, no entry/size/exit
gated (flow_not_block).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from polaris.scripts._production_tick import _log_tick_stage_fault

_SRC = Path("polaris/scripts/_production_tick.py").read_text()


# ---------------------------------------------------------------------------
# 1 — _log_tick_stage_fault: stage name + real exception detail, not %r-only.
# ---------------------------------------------------------------------------


def test_log_tick_stage_fault_includes_stage_name_and_exception_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="polaris.scripts._production_tick"):
        try:
            raise ValueError("simulated database is locked")
        except ValueError as exc:
            _log_tick_stage_fault("cf_sweep_forward_marks", 42, exc)
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "cf_sweep_forward_marks" in msg, "stage name must be in the log line"
    assert "42" in msg, "tick index must be in the log line"
    assert "ValueError" in msg, (
        "exception TYPE must be logged (not repr-only) — this is what lets an "
        "operator tell 'database is locked' apart from any other fault class"
    )
    assert "simulated database is locked" in msg, (
        "exception MESSAGE must be logged (statement-level detail, not "
        "repr-only) so the specific failing operation is diagnosable"
    )


def test_log_tick_stage_fault_distinguishes_different_stages() -> None:
    """Two different stages failing must produce DISTINGUISHABLE log lines —
    the pre-fix outer catch-all logged only '[tick %d] error: %r', which is
    identical no matter WHICH internal statement raised."""
    import logging as _logging

    records: list[str] = []

    class _Capture(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = _logging.getLogger("polaris.scripts._production_tick")
    handler = _Capture()
    logger.addHandler(handler)
    try:
        _log_tick_stage_fault("recalc_active_positions_ai", 1, RuntimeError("r1"))
        _log_tick_stage_fault("capital_rotation", 1, RuntimeError("r2"))
    finally:
        logger.removeHandler(handler)
    assert records[0] != records[1]
    assert "recalc_active_positions_ai" in records[0]
    assert "capital_rotation" in records[1]


# ---------------------------------------------------------------------------
# 2 — source-position wiring: each discrete stage is individually isolated.
# ---------------------------------------------------------------------------


def _assert_wrapped_in_own_try(marker: str, stage_name: str) -> None:
    """The call containing ``marker`` must be preceded (within a small
    lookback window) by a ``try:`` and followed by an
    ``except Exception as exc:`` whose block calls ``_log_tick_stage_fault``
    with ``stage_name`` — i.e. its own isolated try/except, not a shared one
    guarding unrelated code."""
    call_idx = _SRC.index(marker)
    lookback_start = max(0, call_idx - 200)
    preceding = _SRC[lookback_start:call_idx]
    assert "try:" in preceding, f"{marker!r} must be preceded by its own try:"
    lookahead_end = min(len(_SRC), call_idx + 800)
    following = _SRC[call_idx:lookahead_end]
    assert "except Exception as exc:" in following, (
        f"{marker!r} must be followed by its own except block"
    )
    assert f'_log_tick_stage_fault("{stage_name}"' in following, (
        f"{marker!r}'s except block must call _log_tick_stage_fault with "
        f"stage={stage_name!r}"
    )


def test_cf_sweep_forward_marks_isolated() -> None:
    _assert_wrapped_in_own_try(
        "await sweep_forward_marks(conn, now_ts=now_ts)", "cf_sweep_forward_marks",
    )


def test_recalc_deterministic_isolated() -> None:
    _assert_wrapped_in_own_try(
        "await run_recalc_for_active_positions(conn, now_ts=now_ts)",
        "recalc_active_positions_deterministic",
    )


def test_recalc_ai_isolated() -> None:
    _assert_wrapped_in_own_try(
        "await recalc_active_positions(", "recalc_active_positions_ai",
    )


def test_capital_rotation_isolated() -> None:
    _assert_wrapped_in_own_try(
        "await rotation.evaluate_capital_rotation(", "capital_rotation",
    )


def test_evaluate_swaps_isolated() -> None:
    _assert_wrapped_in_own_try(
        "_evaluate_swaps(conn, now_ts=now_ts)", "evaluate_swaps",
    )


def test_regime_compute_loop_isolated_per_symbol() -> None:
    """The regime-compute loop body must be per-ITERATION isolated (one
    symbol's fault must not abort the rest of the focus list)."""
    call_idx = _SRC.index("regime_by_group[(venue, group_id)] = compute_and_flip_regime(")
    lookback_start = max(0, call_idx - 900)
    preceding = _SRC[lookback_start:call_idx]
    assert "try:" in preceding
    lookahead_end = min(len(_SRC), call_idx + 500)
    following = _SRC[call_idx:lookahead_end]
    assert "except Exception as exc:" in following
    assert '_log_tick_stage_fault(f"regime_compute[' in following


def test_dispatch_prefetch_isolated_per_symbol_timeframe() -> None:
    """The per-(venue,symbol,timeframe) prefetch (bar read + market-view
    build) must be individually isolated — a DB-lock fault reading ONE
    symbol's bars must not abort dispatch for every other queued symbol."""
    call_idx = _SRC.index("bars = await read_recent_bars_ondemand(")
    lookback_start = max(0, call_idx - 400)
    preceding = _SRC[lookback_start:call_idx]
    assert "try:" in preceding
    lookahead_end = min(len(_SRC), call_idx + 1800)
    following = _SRC[call_idx:lookahead_end]
    assert "except Exception as exc:" in following
    assert "_log_tick_stage_fault(" in following
    assert 'f"dispatch_prefetch[' in following


def test_stage_isolation_preserves_existing_pregate_ordering() -> None:
    """The spec_a prefetch pre-gate (bucket_has_exempt) must still sit BEFORE
    the (now try-wrapped) bar read — regression guard for
    test_prefetch_pregate_precedes_bar_read in the compute-scheduling suite."""
    gate_idx = _SRC.index("bucket_has_exempt")
    read_idx = _SRC.index("bars = await read_recent_bars_ondemand(")
    assert gate_idx < read_idx


def test_generate_raw_signal_isolation_untouched() -> None:
    """The pre-existing per-strategy generate_raw_signal isolation (with its
    own record_fault/circuit-breaker semantics) must be UNCHANGED by this
    fix — fix #3 only ADDS isolation around stages that had none."""
    assert "except Exception as exc:  # noqa: BLE001 — must isolate" in _SRC
    call_idx = _SRC.index("sig = strategy.generate_raw_signal(mv)")
    following = _SRC[call_idx : call_idx + 400]
    assert "record_fault(" in following
    assert "FAULT_EXCEPTION" in following
