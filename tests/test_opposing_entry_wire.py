"""Opposite-aware entry WIRE — ``handle_opposing_entries`` + the ``_run_tick``
integration seam.

DEMO/PAPER ONLY — virtual funds. Forensic (Jin 2026-07-10): US500 held long x2
+ short x1 simultaneously — ``session_breakout`` entered long at 14:11 then
the SAME strategy flipped short on the SAME symbol at 14:17 (self-hedge);
``concurrent_same_side_open`` only ever compared ``side = ?`` so the opposite
position was invisible to every existing entry-gating check.

Pins the 4 TDD cases from the spec:
1. SAME strategy + opposite signal -> the EXISTING victim is closed
   (``close_reason='signal_flip'``) BEFORE the new entry proceeds (order).
2. DIFFERENT strategy + opposite signal -> BOTH stay open, instrumented only.
3. SAME direction -> unaffected (byte-identical to pre-existing behaviour).
4. OKX (long-only) -> a structural no-op (no short position can ever exist).

``flow_not_block``: ``handle_opposing_entries`` NEVER blocks/skips the
caller's entry — it has no return value a caller could branch on, and even a
``close_specific`` exception is swallowed (fail-open) rather than propagated.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock

from polaris.scripts._production_state import ProdLoopState
from polaris.scripts._production_tick_opposing import handle_opposing_entries


def _insert_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    venue: str = "capital",
    symbol: str = "US500",
    strategy_id: str = "session_breakout",
    side: str = "long",
    status: str = "open",
) -> None:
    conn.execute(
        "INSERT INTO positions ("
        "position_id, venue, symbol, underlying_group_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts"
        ") VALUES (?, ?, ?, '', ?, ?, ?, ?, 1.0, ?, 1000)",
        (
            position_id, venue, symbol, strategy_id,
            strategy_id, strategy_id, side, status,
        ),
    )


def _lookup_regime(conn: sqlite3.Connection, venue: str, symbol: str) -> str:
    return "chop"


# ===========================================================================
# 1. SAME strategy + opposite side -> FLIP (close-then-let-entry-proceed)
# ===========================================================================


async def test_flip_closes_same_strategy_victim_with_signal_flip_reason(
    memdb: sqlite3.Connection,
) -> None:
    _insert_position(
        memdb, position_id="p_long", side="long", strategy_id="session_breakout",
    )
    state = ProdLoopState()
    close_mock = AsyncMock(return_value=True)
    await handle_opposing_entries(
        memdb, state=state, venue="capital", symbol="US500",
        strategy_id="session_breakout", side="short", now_ts=int(time.time()),
        lookup_regime=_lookup_regime, close_specific=close_mock,
    )
    close_mock.assert_awaited_once()
    assert close_mock.await_args.kwargs["position_id"] == "p_long"
    assert close_mock.await_args.kwargs["close_reason"] == "signal_flip"


async def test_flip_records_risk_event_with_closed_true(
    memdb: sqlite3.Connection,
) -> None:
    _insert_position(memdb, position_id="p_long", strategy_id="session_breakout")
    state = ProdLoopState()
    close_mock = AsyncMock(return_value=True)
    await handle_opposing_entries(
        memdb, state=state, venue="capital", symbol="US500",
        strategy_id="session_breakout", side="short", now_ts=int(time.time()),
        lookup_regime=_lookup_regime, close_specific=close_mock,
    )
    row = memdb.execute(
        "SELECT strategy_id, event_type, payload_json FROM risk_events"
    ).fetchone()
    assert row[0] == "session_breakout"
    assert row[1] == "opposing_entry_flip"
    assert '"closed":true' in row[2]


async def test_flip_still_records_event_when_close_fails(
    memdb: sqlite3.Connection,
) -> None:
    """A failed close attempt is instrumented (closed=False) but NEVER raises
    and NEVER blocks — the caller (``_run_tick``) always falls through to the
    new entry regardless of this outcome (flow_not_block)."""
    _insert_position(memdb, position_id="p_long", strategy_id="session_breakout")
    state = ProdLoopState()
    close_mock = AsyncMock(return_value=False)
    await handle_opposing_entries(
        memdb, state=state, venue="capital", symbol="US500",
        strategy_id="session_breakout", side="short", now_ts=int(time.time()),
        lookup_regime=_lookup_regime, close_specific=close_mock,
    )
    close_mock.assert_awaited_once()
    row = memdb.execute(
        "SELECT event_type, payload_json FROM risk_events"
    ).fetchone()
    assert row[0] == "opposing_entry_flip"
    assert '"closed":false' in row[1]


async def test_flip_close_exception_is_swallowed_never_blocks(
    memdb: sqlite3.Connection,
) -> None:
    """close_specific raising (e.g. venue timeout) must not propagate — the
    entry seam this is called from has no wrapping try/except of its own."""
    _insert_position(memdb, position_id="p_long", strategy_id="session_breakout")
    state = ProdLoopState()
    close_mock = AsyncMock(side_effect=RuntimeError("venue timeout"))
    await handle_opposing_entries(  # must not raise
        memdb, state=state, venue="capital", symbol="US500",
        strategy_id="session_breakout", side="short", now_ts=int(time.time()),
        lookup_regime=_lookup_regime, close_specific=close_mock,
    )
    close_mock.assert_awaited_once()
    row = memdb.execute(
        "SELECT event_type, payload_json FROM risk_events"
    ).fetchone()
    # Instrumented as a failed flip (closed=False), not silently dropped.
    assert row[0] == "opposing_entry_flip"
    assert '"closed":false' in row[1]


# ===========================================================================
# 2. DIFFERENT strategy + opposite side -> COEXIST (both stay open)
# ===========================================================================


async def test_coexist_different_strategy_does_not_close(
    memdb: sqlite3.Connection,
) -> None:
    _insert_position(memdb, position_id="p_long", strategy_id="session_breakout")
    state = ProdLoopState()
    close_mock = AsyncMock(return_value=True)
    await handle_opposing_entries(
        memdb, state=state, venue="capital", symbol="US500",
        strategy_id="volume_burst", side="short", now_ts=int(time.time()),
        lookup_regime=_lookup_regime, close_specific=close_mock,
    )
    close_mock.assert_not_awaited()


async def test_coexist_records_risk_event_carrying_both_strategy_ids(
    memdb: sqlite3.Connection,
) -> None:
    _insert_position(memdb, position_id="p_long", strategy_id="session_breakout")
    state = ProdLoopState()
    close_mock = AsyncMock(return_value=True)
    await handle_opposing_entries(
        memdb, state=state, venue="capital", symbol="US500",
        strategy_id="volume_burst", side="short", now_ts=int(time.time()),
        lookup_regime=_lookup_regime, close_specific=close_mock,
    )
    row = memdb.execute(
        "SELECT strategy_id, event_type, payload_json FROM risk_events"
    ).fetchone()
    assert row[0] == "volume_burst"  # the entering (new) strategy
    assert row[1] == "opposing_entry_coexist"
    assert '"opposing_strategy_id":"session_breakout"' in row[2]
    assert '"closed":null' in row[2]
    # The victim position itself is untouched (still open — coexistence).
    status = memdb.execute(
        "SELECT status FROM positions WHERE position_id='p_long'"
    ).fetchone()[0]
    assert status == "open"


# ===========================================================================
# 3. SAME direction -> unaffected
# ===========================================================================


async def test_same_side_is_a_complete_noop(memdb: sqlite3.Connection) -> None:
    _insert_position(
        memdb, position_id="p_long", side="long", strategy_id="session_breakout",
    )
    state = ProdLoopState()
    close_mock = AsyncMock(return_value=True)
    await handle_opposing_entries(
        memdb, state=state, venue="capital", symbol="US500",
        strategy_id="session_breakout", side="long", now_ts=int(time.time()),
        lookup_regime=_lookup_regime, close_specific=close_mock,
    )
    close_mock.assert_not_awaited()
    assert memdb.execute("SELECT COUNT(*) FROM risk_events").fetchone()[0] == 0


async def test_no_opposing_position_is_a_complete_noop(
    memdb: sqlite3.Connection,
) -> None:
    state = ProdLoopState()
    close_mock = AsyncMock(return_value=True)
    await handle_opposing_entries(
        memdb, state=state, venue="capital", symbol="US500",
        strategy_id="session_breakout", side="short", now_ts=int(time.time()),
        lookup_regime=_lookup_regime, close_specific=close_mock,
    )
    close_mock.assert_not_awaited()
    assert memdb.execute("SELECT COUNT(*) FROM risk_events").fetchone()[0] == 0


# ===========================================================================
# 4. OKX (long-only) -> structural no-op
# ===========================================================================


async def test_okx_long_only_never_flips(memdb: sqlite3.Connection) -> None:
    """OKX strategies hardcode side='long' at signal-generation time (SPOT is
    long-only), so an OKX long position can never find an opposite — no
    close, no event."""
    _insert_position(
        memdb, position_id="p_okx", venue="okx", symbol="BTC-USDT",
        side="long", strategy_id="donchian_turtle_breakout",
    )
    state = ProdLoopState()
    close_mock = AsyncMock(return_value=True)
    await handle_opposing_entries(
        memdb, state=state, venue="okx", symbol="BTC-USDT",
        strategy_id="donchian_turtle_breakout", side="long",
        now_ts=int(time.time()), lookup_regime=_lookup_regime,
        close_specific=close_mock,
    )
    close_mock.assert_not_awaited()
    assert memdb.execute("SELECT COUNT(*) FROM risk_events").fetchone()[0] == 0


# ===========================================================================
# Multiple opposing positions — mixed same/different strategy in one call
# ===========================================================================


async def test_mixed_victims_flip_one_coexist_other(
    memdb: sqlite3.Connection,
) -> None:
    _insert_position(
        memdb, position_id="p_same", side="long", strategy_id="session_breakout",
    )
    _insert_position(
        memdb, position_id="p_other", side="long", strategy_id="volume_burst",
    )
    state = ProdLoopState()
    close_mock = AsyncMock(return_value=True)
    await handle_opposing_entries(
        memdb, state=state, venue="capital", symbol="US500",
        strategy_id="session_breakout", side="short", now_ts=int(time.time()),
        lookup_regime=_lookup_regime, close_specific=close_mock,
    )
    close_mock.assert_awaited_once()
    assert close_mock.await_args.kwargs["position_id"] == "p_same"
    rows = memdb.execute(
        "SELECT event_type FROM risk_events ORDER BY event_type"
    ).fetchall()
    assert [r[0] for r in rows] == [
        "opposing_entry_coexist", "opposing_entry_flip",
    ]


# ===========================================================================
# Source-level lint: wired into ``_run_tick`` at the right point + ordering.
# ===========================================================================


def test_handle_opposing_entries_wired_into_run_tick() -> None:
    src = Path("polaris/scripts/_production_tick.py").read_text()
    assert "handle_opposing_entries" in src, (
        "opposite-aware entry must be wired into the _run_tick entry seam."
    )


def test_opposing_entries_called_before_pipeline_dispatch() -> None:
    """close-then-open ordering: the opposing-entry reaction must run BEFORE
    the signal is appended to ``pipeline_specs`` (the actual order dispatch),
    so a same-strategy FLIP close is always submitted ahead of the new open."""
    src = Path("polaris/scripts/_production_tick.py").read_text()
    call_idx = src.index("await handle_opposing_entries(")
    dispatch_idx = src.index("pipeline_specs.append(")
    assert call_idx < dispatch_idx


def test_opposing_entries_called_after_the_skip_gates() -> None:
    """The reaction must run AFTER concurrent_same_side_open / reentry_cooldown
    / rotation_vacated_cooldown have all passed — it must never fire (and
    close a victim) for a signal that is about to be skipped anyway."""
    src = Path("polaris/scripts/_production_tick.py").read_text()
    call_idx = src.index("await handle_opposing_entries(")
    concurrent_idx = src.index("reason=concurrent_same_side_open")
    cooldown_idx = src.index("reason=reentry_cooldown")
    vacated_idx = src.index("reason=rotation_vacated_cooldown")
    assert concurrent_idx < call_idx
    assert cooldown_idx < call_idx
    assert vacated_idx < call_idx
