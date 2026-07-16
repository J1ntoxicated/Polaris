"""Source-level wiring lint — the delegation gate SHADOW (P2b) actually
reaches the live ``_run_tick`` G2 dispatch loop, sitting AFTER the signal is
persisted (``persist_emitted_signal``) — mirrors
``test_tsmom_literature_shadow_wiring.py``'s pattern. Registered != dispatched
is the exact silent-INERT failure mode this lint guards against
([[feedback_verify_firing_after_build]]).

DEMO/PAPER · behavior-0 · flow_not_block · 9-stack ban untouched.
"""

from __future__ import annotations

from pathlib import Path

_SRC = Path("polaris/scripts/_production_tick.py").read_text()


def test_log_delegation_shadow_imported_and_called() -> None:
    assert "from polaris.core.delegation.shadow import log_delegation_shadow" in _SRC
    assert "log_delegation_shadow(" in _SRC


def test_shadow_call_sits_after_persist_emitted_signal() -> None:
    persist_idx = _SRC.index("persist_emitted_signal(")
    call_idx = _SRC.index("log_delegation_shadow(")
    assert persist_idx < call_idx


def test_shadow_call_dispatches_the_actual_firing_strategy_id() -> None:
    """dispatched_strategy_id must be THIS iteration's strategy_id (the one
    that just emitted), not some other name — the whole point of the shadow
    row is comparing fit-top against the strategy that ACTUALLY fired."""
    call_idx = _SRC.index("log_delegation_shadow(")
    close_idx = _SRC.index(")", _SRC.index("write_conn=", call_idx))
    segment = _SRC[call_idx:close_idx]
    assert "dispatched_strategy_id=strategy_id" in segment


def test_shadow_call_writes_marketdata_domain_not_trading_conn() -> None:
    """storage-split: the WRITE path must be state.md_conn / state.md_db_writer
    (marketdata), never the trading ``conn`` — mirrors every other
    ``*_shadow`` table's wiring post-2026-07-14 split."""
    call_idx = _SRC.index("log_delegation_shadow(")
    close_idx = _SRC.index(")", _SRC.index("write_conn=", call_idx))
    segment = _SRC[call_idx:close_idx]
    assert "write_conn=state.md_conn" in segment
    assert "db_writer=state.md_db_writer" in segment


def test_shadow_call_never_feeds_a_sizing_or_gating_seam() -> None:
    """Behavior-0 structural check: the call's own statement block must not
    reference sizing_hint/strength — read-only instrumentation."""
    call_idx = _SRC.index("log_delegation_shadow(")
    close_idx = _SRC.index(")", _SRC.index("write_conn=", call_idx))
    segment = _SRC[call_idx:close_idx]
    assert "sizing_hint" not in segment
    assert "strength" not in segment
