"""P1 re-entry cooldown — over-trading / turnover-cost guard tests.

DEMO/PAPER. The guard removes *duplicate* opens on the same
(venue, symbol, strategy_id) inside a short window; it never shrinks size,
never halts on P&L, and exempts strong signals (AGGRESSIVE flow preserved).
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from polaris.core.isolation.reentry import (
    REENTRY_COOLDOWN_SEC,
    reentry_cooldown_active,
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
    venue: str,
    symbol: str,
    strategy_id: str,
    opened_ts: int,
    status: str = "open",
) -> None:
    conn.execute(
        "INSERT INTO positions ("
        "position_id, venue, symbol, underlying_group_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts"
        ") VALUES (?, ?, ?, '', ?, ?, ?, 'long', 1.0, ?, ?)",
        (
            uuid.uuid4().hex, venue, symbol, strategy_id,
            strategy_id, strategy_id, status, opened_ts,
        ),
    )


def test_within_window_blocks(conn: sqlite3.Connection) -> None:
    _insert_position(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        opened_ts=1000,
    )
    # 40s later, well inside the 300s window → skip.
    assert reentry_cooldown_active(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        now_ts=1040, cooldown_sec=300, exempt=False,
    ) is True


def test_after_window_allows(conn: sqlite3.Connection) -> None:
    _insert_position(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        opened_ts=1000,
    )
    assert reentry_cooldown_active(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        now_ts=1000 + 300, cooldown_sec=300, exempt=False,
    ) is False  # exactly at boundary (>=) is allowed
    assert reentry_cooldown_active(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        now_ts=1000 + 999, cooldown_sec=300, exempt=False,
    ) is False


def test_no_prior_position_allows(conn: sqlite3.Connection) -> None:
    assert reentry_cooldown_active(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        now_ts=5000, cooldown_sec=300, exempt=False,
    ) is False


def test_strong_signal_exempt_always_allows(conn: sqlite3.Connection) -> None:
    _insert_position(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        opened_ts=1000,
    )
    # Inside window but exempt → never blocked (flow preserved).
    assert reentry_cooldown_active(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        now_ts=1010, cooldown_sec=300, exempt=True,
    ) is False


def test_key_isolation_other_symbol_and_strategy(
    conn: sqlite3.Connection,
) -> None:
    _insert_position(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        opened_ts=1000,
    )
    # Different symbol — unaffected.
    assert reentry_cooldown_active(
        conn, venue="okx", symbol="ETH-USDT", strategy_id="volume_burst",
        now_ts=1010, cooldown_sec=300, exempt=False,
    ) is False
    # Different strategy — unaffected.
    assert reentry_cooldown_active(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="tsmom",
        now_ts=1010, cooldown_sec=300, exempt=False,
    ) is False
    # Different venue — unaffected.
    assert reentry_cooldown_active(
        conn, venue="capital", symbol="SOL-USDT", strategy_id="volume_burst",
        now_ts=1010, cooldown_sec=300, exempt=False,
    ) is False


def test_closed_position_still_counts(conn: sqlite3.Connection) -> None:
    # status-agnostic: a recently CLOSED position must also start the cooldown
    # (re-buying right after a close is the over-trading we block).
    _insert_position(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        opened_ts=2000, status="closed",
    )
    assert reentry_cooldown_active(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        now_ts=2050, cooldown_sec=300, exempt=False,
    ) is True


def test_cooldown_disabled_when_zero(conn: sqlite3.Connection) -> None:
    _insert_position(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        opened_ts=1000,
    )
    assert reentry_cooldown_active(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        now_ts=1010, cooldown_sec=0, exempt=False,
    ) is False


def test_default_cooldown_is_300() -> None:
    assert REENTRY_COOLDOWN_SEC == 300


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    import polaris.core.isolation.reentry as reentry_mod

    monkeypatch.setenv("POLARIS_REENTRY_COOLDOWN_SEC", "60")
    reloaded = importlib.reload(reentry_mod)
    try:
        assert reloaded.REENTRY_COOLDOWN_SEC == 60
    finally:
        monkeypatch.delenv("POLARIS_REENTRY_COOLDOWN_SEC", raising=False)
        importlib.reload(reentry_mod)
