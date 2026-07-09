"""Opposite-aware entry — core DB helpers (pure, no orchestration).

DEMO/PAPER ONLY — virtual funds. Forensic (Jin 2026-07-10): US500 held long x2
+ short x1 simultaneously (self-hedge); ``concurrent_same_side_open`` only
ever compared ``side = ?`` so an opposite-side position was structurally
invisible. These tests pin the pure DB layer
(:mod:`polaris.core.isolation.opposing_entry`): the opposite-side live-
position lookup + the best-effort ``risk_events`` instrumentation write.
flow_not_block: neither function ever raises on bad input / DB trouble — both
fail open (empty list / silent insert-skip) so a caller can never be wedged.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from polaris.core.isolation.opposing_entry import (
    EVENT_COEXIST,
    EVENT_FLIP,
    OpposingPosition,
    opposing_side_open_positions,
    record_opposing_entry_event,
)
from polaris.storage.schema import ALL_DDL


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        c.execute(stmt)
    return c


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


# ===========================================================================
# opposing_side_open_positions
# ===========================================================================


def test_finds_opposite_side_same_strategy(conn: sqlite3.Connection) -> None:
    _insert_position(
        conn, position_id="p_long", side="long", strategy_id="session_breakout",
    )
    result = opposing_side_open_positions(
        conn, venue="capital", symbol="US500", side="short",
    )
    assert result == [OpposingPosition("p_long", "session_breakout")]


def test_finds_opposite_side_different_strategy(conn: sqlite3.Connection) -> None:
    _insert_position(
        conn, position_id="p_long", side="long", strategy_id="session_breakout",
    )
    result = opposing_side_open_positions(
        conn, venue="capital", symbol="US500", side="short",
    )
    assert result[0].strategy_id == "session_breakout"
    # A NEW entry from a DIFFERENT strategy still sees the same opposite row —
    # the lookup itself carries no strategy filter (the caller decides
    # flip-vs-coexist from the returned strategy_id).
    result2 = opposing_side_open_positions(
        conn, venue="capital", symbol="US500", side="short",
    )
    assert result2 == result


def test_same_side_is_not_opposing(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p_long", side="long")
    result = opposing_side_open_positions(
        conn, venue="capital", symbol="US500", side="long",
    )
    assert result == []


def test_closed_position_not_opposing(conn: sqlite3.Connection) -> None:
    _insert_position(
        conn, position_id="p_closed", side="long", status="closed",
    )
    result = opposing_side_open_positions(
        conn, venue="capital", symbol="US500", side="short",
    )
    assert result == []


def test_cancelled_and_reconciled_excluded(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p_cx", side="long", status="cancelled")
    _insert_position(conn, position_id="p_rc", side="long", status="reconciled")
    result = opposing_side_open_positions(
        conn, venue="capital", symbol="US500", side="short",
    )
    assert result == []


def test_different_symbol_not_opposing(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p_long", side="long", symbol="EURUSD")
    result = opposing_side_open_positions(
        conn, venue="capital", symbol="US500", side="short",
    )
    assert result == []


def test_different_venue_not_opposing(conn: sqlite3.Connection) -> None:
    _insert_position(conn, position_id="p_long", side="long", venue="okx")
    result = opposing_side_open_positions(
        conn, venue="capital", symbol="US500", side="short",
    )
    assert result == []


def test_okx_long_only_never_has_an_opposite(conn: sqlite3.Connection) -> None:
    """OKX spot is long-only by strategy construction (allow_short=False) — a
    long position can never find a 'short' opposite. No venue special-case is
    needed in the lookup itself; this is a structural no-op verified here."""
    _insert_position(
        conn, position_id="p_okx_long", venue="okx", symbol="BTC-USDT",
        side="long", strategy_id="donchian_turtle_breakout",
    )
    result = opposing_side_open_positions(
        conn, venue="okx", symbol="BTC-USDT", side="long",
    )
    assert result == []


def test_db_error_fails_open_to_empty_list() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)  # no schema
    result = opposing_side_open_positions(
        conn, venue="capital", symbol="US500", side="short",
    )
    assert result == []


# ===========================================================================
# record_opposing_entry_event
# ===========================================================================


def test_record_flip_event_writes_risk_events_row(conn: sqlite3.Connection) -> None:
    record_opposing_entry_event(
        conn, strategy_id="session_breakout", event_type=EVENT_FLIP,
        venue="capital", symbol="US500", side="short",
        opposing_position_id="p_long", opposing_strategy_id="session_breakout",
        now_ts=1700, closed=True,
    )
    rows = conn.execute(
        "SELECT strategy_id, event_type, created_ts, payload_json "
        "FROM risk_events"
    ).fetchall()
    assert len(rows) == 1
    strategy_id, event_type, created_ts, payload_json = rows[0]
    assert strategy_id == "session_breakout"
    assert event_type == "opposing_entry_flip"
    assert created_ts == 1700
    payload = json.loads(payload_json)
    assert payload["venue"] == "capital"
    assert payload["symbol"] == "US500"
    assert payload["side"] == "short"
    assert payload["opposing_position_id"] == "p_long"
    assert payload["closed"] is True


def test_record_coexist_event_carries_closed_none(conn: sqlite3.Connection) -> None:
    record_opposing_entry_event(
        conn, strategy_id="volume_burst", event_type=EVENT_COEXIST,
        venue="capital", symbol="US500", side="short",
        opposing_position_id="p_long", opposing_strategy_id="session_breakout",
        now_ts=1700,
    )
    row = conn.execute(
        "SELECT event_type, payload_json FROM risk_events"
    ).fetchone()
    assert row[0] == "opposing_entry_coexist"
    payload = json.loads(row[1])
    assert payload["closed"] is None
    assert payload["opposing_strategy_id"] == "session_breakout"


def test_record_event_id_is_unique_per_call(conn: sqlite3.Connection) -> None:
    for _ in range(3):
        record_opposing_entry_event(
            conn, strategy_id="s", event_type=EVENT_COEXIST,
            venue="capital", symbol="US500", side="short",
            opposing_position_id="p", opposing_strategy_id="other",
            now_ts=1700,
        )
    ids = [
        r[0] for r in conn.execute("SELECT risk_event_id FROM risk_events")
    ]
    assert len(ids) == 3
    assert len(set(ids)) == 3
    assert all(uuid.UUID(i) for i in ids)  # well-formed uuid4 hex


def test_record_event_db_error_is_swallowed() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)  # no schema
    # Must not raise even though risk_events does not exist.
    record_opposing_entry_event(
        conn, strategy_id="s", event_type=EVENT_FLIP,
        venue="capital", symbol="US500", side="short",
        opposing_position_id="p", opposing_strategy_id="s",
        now_ts=1700, closed=False,
    )
