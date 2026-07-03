"""Per-ticker tailored concurrent-open cap — DEMO/PAPER, aggressive bias.

Fixes the uniform entry-gating skip on ``concurrent_same_side_open``: instead
of a flat max-1-live-position rule for every (venue, symbol, strategy_id), a
name with a PROVEN own win-rate (>= CS3_N_THRESHOLD closed trades, same sample
floor as sizing's Cold-Start CS-3) earns a controlled 2nd concurrent slot.
This is NOT a uniform loosening — a thin-sample or weak-edge name gets no
extra room and keeps the original cap of 1 (flow_not_block: precision widen,
never a blanket relax).
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.isolation.reentry import (
    TAILORED_CAP_CEILING,
    TAILORED_CAP_WIN_RATE_FLOOR,
    concurrent_same_side_open,
    tailored_concurrent_cap,
)
from polaris.core.sizing.schema import CS3_N_THRESHOLD
from polaris.storage.schema import ALL_DDL


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        c.execute(stmt)
    return c


def _insert_closed(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    pnl_r: float,
    side: str = "long",
) -> None:
    conn.execute(
        "INSERT INTO positions ("
        "position_id, venue, symbol, underlying_group_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "pnl_r"
        ") VALUES (?, ?, ?, '', ?, ?, ?, ?, 1.0, 'closed', 1000, ?)",
        (
            uuid.uuid4().hex, venue, symbol, strategy_id,
            strategy_id, strategy_id, side, pnl_r,
        ),
    )


def _insert_open(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    side: str = "long",
) -> None:
    conn.execute(
        "INSERT INTO positions ("
        "position_id, venue, symbol, underlying_group_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts"
        ") VALUES (?, ?, ?, '', ?, ?, ?, ?, 1.0, 'open', 1000)",
        (
            uuid.uuid4().hex, venue, symbol, strategy_id,
            strategy_id, strategy_id, side,
        ),
    )


# --- tailored_concurrent_cap -------------------------------------------------


def test_no_history_caps_at_one(conn: sqlite3.Connection) -> None:
    assert tailored_concurrent_cap(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="donchian_turtle_breakout",
    ) == 1


def test_thin_sample_below_threshold_caps_at_one(conn: sqlite3.Connection) -> None:
    # All wins, but n < CS3_N_THRESHOLD — sample too thin to trust.
    for _ in range(CS3_N_THRESHOLD - 1):
        _insert_closed(
            conn, venue="okx", symbol="SOL-USDT",
            strategy_id="donchian_turtle_breakout", pnl_r=1.0,
        )
    assert tailored_concurrent_cap(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="donchian_turtle_breakout",
    ) == 1


def test_proven_edge_widens_to_ceiling(conn: sqlite3.Connection) -> None:
    # n >= threshold, win-rate 100% >= floor → widened.
    for _ in range(CS3_N_THRESHOLD):
        _insert_closed(
            conn, venue="okx", symbol="SOL-USDT",
            strategy_id="donchian_turtle_breakout", pnl_r=1.0,
        )
    assert tailored_concurrent_cap(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="donchian_turtle_breakout",
    ) == TAILORED_CAP_CEILING


def test_weak_edge_at_full_sample_stays_at_one(conn: sqlite3.Connection) -> None:
    # n >= threshold but win-rate BELOW the floor — no extra room.
    n_losses = CS3_N_THRESHOLD
    for _ in range(n_losses):
        _insert_closed(
            conn, venue="okx", symbol="SOL-USDT",
            strategy_id="donchian_turtle_breakout", pnl_r=-1.0,
        )
    assert tailored_concurrent_cap(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="donchian_turtle_breakout",
    ) == 1


def test_win_rate_exactly_at_floor_widens(conn: sqlite3.Connection) -> None:
    # Construct exactly TAILORED_CAP_WIN_RATE_FLOOR win-rate at n=CS3_N_THRESHOLD.
    n = CS3_N_THRESHOLD
    wins = round(TAILORED_CAP_WIN_RATE_FLOOR * n)
    for _ in range(wins):
        _insert_closed(
            conn, venue="okx", symbol="ETH-USDT",
            strategy_id="tsmom", pnl_r=1.0,
        )
    for _ in range(n - wins):
        _insert_closed(
            conn, venue="okx", symbol="ETH-USDT",
            strategy_id="tsmom", pnl_r=-1.0,
        )
    assert wins / n >= TAILORED_CAP_WIN_RATE_FLOOR
    assert tailored_concurrent_cap(
        conn, venue="okx", symbol="ETH-USDT", strategy_id="tsmom",
    ) == TAILORED_CAP_CEILING


def test_key_isolation_other_symbol_unaffected(conn: sqlite3.Connection) -> None:
    for _ in range(CS3_N_THRESHOLD):
        _insert_closed(
            conn, venue="okx", symbol="SOL-USDT",
            strategy_id="donchian_turtle_breakout", pnl_r=1.0,
        )
    # A different symbol has no history of its own — stays at 1.
    assert tailored_concurrent_cap(
        conn, venue="okx", symbol="ADA-USDT", strategy_id="donchian_turtle_breakout",
    ) == 1
    # A different strategy on the SAME symbol also has no history of its own.
    assert tailored_concurrent_cap(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="bar_breakout_run",
    ) == 1


def test_open_positions_excluded_from_sample(conn: sqlite3.Connection) -> None:
    # Only CLOSED rows with a non-NULL pnl_r count toward the sample.
    for _ in range(CS3_N_THRESHOLD):
        _insert_open(conn, venue="okx", symbol="SOL-USDT", strategy_id="tsmom")
    assert tailored_concurrent_cap(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="tsmom",
    ) == 1


# --- concurrent_same_side_open with a tailored cap --------------------------


def test_default_cap_is_byte_identical_to_original_behaviour(
    conn: sqlite3.Connection,
) -> None:
    _insert_open(conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom")
    # No cap kwarg passed — the original one-live-position rule.
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom", side="long",
    ) is True


def test_cap_two_allows_a_second_concurrent_open(conn: sqlite3.Connection) -> None:
    _insert_open(conn, venue="okx", symbol="SOL-USDT", strategy_id="tsmom")
    # One already open, cap=2 → a second is allowed (not yet at cap).
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="tsmom", side="long",
        cap=2,
    ) is False


def test_cap_two_blocks_a_third_concurrent_open(conn: sqlite3.Connection) -> None:
    _insert_open(conn, venue="okx", symbol="SOL-USDT", strategy_id="tsmom")
    _insert_open(conn, venue="okx", symbol="SOL-USDT", strategy_id="tsmom")
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="tsmom", side="long",
        cap=2,
    ) is True


def test_cap_never_goes_below_one(conn: sqlite3.Connection) -> None:
    # A pathological cap=0 must not disable the guard entirely.
    _insert_open(conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom")
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom", side="long",
        cap=0,
    ) is True
