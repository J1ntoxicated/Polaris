"""P1 re-entry cooldown — over-trading / turnover-cost guard tests.

DEMO/PAPER. The guard removes *duplicate* opens on the same
(venue, symbol, strategy_id) inside a short window; it never shrinks size,
never halts on P&L, and exempts strong signals (AGGRESSIVE flow preserved).
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Literal

import pytest

from polaris.core.isolation.reentry import (
    REENTRY_COOLDOWN_SEC,
    bar_seconds,
    concurrent_same_side_open,
    is_novel_reentry,
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
    side: str = "long",
) -> None:
    conn.execute(
        "INSERT INTO positions ("
        "position_id, venue, symbol, underlying_group_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts"
        ") VALUES (?, ?, ?, '', ?, ?, ?, ?, 1.0, ?, ?)",
        (
            uuid.uuid4().hex, venue, symbol, strategy_id,
            strategy_id, strategy_id, side, status, opened_ts,
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


def test_okx_tick_scalp_does_not_block_bar_swing_same_symbol_side(
    conn: sqlite3.Connection,
) -> None:
    # STEP1 multi-horizon NO-BLIND-NETTING rail: after the OKX-spot bar carve-out
    # a tick SCALP (micro_reversion) and a bar SWING (tsmom) coexist on the SAME
    # OKX symbol+side as INDEPENDENT logical positions. concurrent_same_side_open
    # is strategy-scoped, so a live scalp must NOT block the swing entry (and vice
    # versa) — distinct strategy_id = distinct logical position + PnL attribution.
    _insert_position(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="micro_reversion",
        opened_ts=1000, side="long",
    )
    # The bar swing on the SAME symbol+side is allowed (different strategy_id).
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom", side="long",
    ) is False
    # But a SECOND micro_reversion long IS refused (same strategy/symbol/side =
    # the duplicate-stack guard, unchanged).
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="micro_reversion",
        side="long",
    ) is True


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


# --- Component B: timeframe-derived cooldown window (bar_seconds) -----------


def test_bar_seconds_known_timeframes() -> None:
    assert bar_seconds("1m") == 60
    assert bar_seconds("5m") == 300
    assert bar_seconds("15m") == 900
    assert bar_seconds("1H") == 3600  # tsmom → kills the 5-6min stacking.


def test_bar_seconds_unknown_falls_back_to_default() -> None:
    # Never degrade to 0 (which would DISABLE the guard) — fall back to 300s.
    assert bar_seconds("bogus") == 300
    assert bar_seconds("") == 300


# --- Component B: novelty-gated re-entry (replaces raw-strength exemption) --


def test_novelty_first_entry_is_novel() -> None:
    # No prior entry on the key → always allowed (first entry).
    assert is_novel_reentry(
        created_at_bar=100, side="long",
        last_entry_bar=None, last_entry_side=None,
    ) is True


def test_novelty_same_bar_same_side_is_not_novel() -> None:
    # CHURN: identical bar id + side (the 5s fan-out re-emitting the same bar)
    # → NOT novel → cooldown applies → re-entry blocked.
    assert is_novel_reentry(
        created_at_bar=100, side="long",
        last_entry_bar=100, last_entry_side="long",
    ) is False


def test_novelty_new_bar_is_novel() -> None:
    # FLOW: a new strategy-timeframe bar carries fresh info → novel → allowed.
    assert is_novel_reentry(
        created_at_bar=101, side="long",
        last_entry_bar=100, last_entry_side="long",
    ) is True


def test_novelty_side_flip_is_novel() -> None:
    # FLOW: thesis reversed (long → short) within the same bar → novel → allowed.
    assert is_novel_reentry(
        created_at_bar=100, side="short",
        last_entry_bar=100, last_entry_side="long",
    ) is True


def test_novelty_does_not_consult_strength() -> None:
    # The novelty signature has NO strength parameter — raw momentum can never
    # self-exempt a same-bar same-side re-buy (the backwards exemption is gone).
    import inspect

    params = set(inspect.signature(is_novel_reentry).parameters)
    assert "strength" not in params
    assert params == {
        "created_at_bar", "side", "last_entry_bar", "last_entry_side",
    }


# --- Component B: no concurrent duplicate (12-concurrent-BTC stacking) ------


def test_concurrent_same_side_open_blocks(conn: sqlite3.Connection) -> None:
    # An already-OPEN same-side position → a clone is refused (one live position
    # per name/strategy/side). Kills the 12-simultaneous-BTC tsmom stacking that
    # a time cooldown alone misses (each clone is on a distinct, novel bar).
    _insert_position(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        opened_ts=1000, side="long",
    )
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom", side="long",
    ) is True


def test_concurrent_no_open_allows(conn: sqlite3.Connection) -> None:
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom", side="long",
    ) is False


def test_concurrent_side_flip_allowed(conn: sqlite3.Connection) -> None:
    # FLOW: holding a long does NOT block a NEW short (the thesis reversed).
    _insert_position(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        opened_ts=1000, side="long",
    )
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom", side="short",
    ) is False


def test_concurrent_closed_position_does_not_block(
    conn: sqlite3.Connection,
) -> None:
    # FLOW: once the held position CLOSES, a re-entry is not blocked by this
    # guard (the cooldown still governs same-bar timing separately).
    _insert_position(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        opened_ts=1000, side="long", status="closed",
    )
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom", side="long",
    ) is False


def test_concurrent_other_name_strategy_unaffected(
    conn: sqlite3.Connection,
) -> None:
    _insert_position(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        opened_ts=1000, side="long",
    )
    # Different symbol / strategy / venue — unaffected (flow_not_block).
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="ETH-USDT", strategy_id="tsmom", side="long",
    ) is False
    assert concurrent_same_side_open(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="volume_burst",
        side="long",
    ) is False
    assert concurrent_same_side_open(
        conn, venue="capital", symbol="BTC-USDT", strategy_id="tsmom",
        side="long",
    ) is False


# --- Component B: the entry-seam decision composed (churn vs flow) ---------
#
# These mirror the exact composition the _production_tick entry seam runs:
#   skip = concurrent_same_side_open(...)
#          OR reentry_cooldown_active(..., exempt=is_novel_reentry(...))
# using the timeframe-derived window. tsmom = 1H (bar_seconds=3600) so a re-buy
# inside the SAME 1H bar is blocked, a NEW bar / side flip flows.


def _seam_skips(
    conn: sqlite3.Connection,
    *,
    venue: str,
    symbol: str,
    strategy_id: str,
    timeframe: str,
    side: Literal["long", "short"],
    created_at_bar: int,
    now_ts: int,
    last_bar: int | None,
    last_side: str | None,
) -> bool:
    if concurrent_same_side_open(
        conn, venue=venue, symbol=symbol, strategy_id=strategy_id, side=side,
    ):
        return True
    return reentry_cooldown_active(
        conn, venue=venue, symbol=symbol, strategy_id=strategy_id,
        now_ts=now_ts, cooldown_sec=bar_seconds(timeframe),
        exempt=is_novel_reentry(
            created_at_bar=created_at_bar, side=side,
            last_entry_bar=last_bar, last_entry_side=last_side,
        ),
    )


def test_seam_blocks_same_bar_high_strength_rebuy(
    conn: sqlite3.Connection,
) -> None:
    # CHURN: tsmom (1H) re-fires the SAME 1H bar 300s later (no new position
    # open yet — the DB row drives the cooldown). High strength does NOT exempt;
    # only novelty does, and the bar id is unchanged → SKIPPED.
    _insert_position(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        opened_ts=1000, status="closed", side="long",
    )
    assert _seam_skips(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        timeframe="1H", side="long", created_at_bar=900, now_ts=1300,
        last_bar=900, last_side="long",  # same bar, same side → not novel
    ) is True


def test_seam_blocks_concurrent_duplicate(conn: sqlite3.Connection) -> None:
    # CHURN: an OPEN same-side tsmom on BTC → a clone is refused even on a NEW
    # bar (the 12-concurrent stacking a time cooldown alone misses).
    _insert_position(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        opened_ts=1000, status="open", side="long",
    )
    assert _seam_skips(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        timeframe="1H", side="long", created_at_bar=901, now_ts=1300,
        last_bar=900, last_side="long",  # NEW bar, but a clone is still live
    ) is True


def test_seam_allows_new_bar(conn: sqlite3.Connection) -> None:
    # FLOW: the prior position CLOSED; a NEW 1H bar carries fresh info → the
    # novelty exemption lets it through even inside the 3600s window.
    _insert_position(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        opened_ts=1000, status="closed", side="long",
    )
    assert _seam_skips(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        timeframe="1H", side="long", created_at_bar=901, now_ts=1300,
        last_bar=900, last_side="long",  # NEW bar → novel → allowed
    ) is False


def test_seam_allows_side_flip(conn: sqlite3.Connection) -> None:
    # FLOW: thesis reversed (long → short) within the same bar; no live long
    # blocks a short → side-flip novelty allows it.
    _insert_position(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        opened_ts=1000, status="closed", side="long",
    )
    assert _seam_skips(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        timeframe="1H", side="short", created_at_bar=900, now_ts=1300,
        last_bar=900, last_side="long",  # same bar but flipped side → novel
    ) is False


def test_seam_allows_first_entry_and_other_name(
    conn: sqlite3.Connection,
) -> None:
    # FLOW: no prior entry (first entry) → allowed; a different symbol entirely
    # is unaffected (flow_not_block).
    assert _seam_skips(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        timeframe="1H", side="long", created_at_bar=900, now_ts=1300,
        last_bar=None, last_side=None,
    ) is False
    _insert_position(
        conn, venue="okx", symbol="BTC-USDT", strategy_id="tsmom",
        opened_ts=1290, status="open", side="long",
    )
    assert _seam_skips(
        conn, venue="okx", symbol="ETH-USDT", strategy_id="tsmom",
        timeframe="1H", side="long", created_at_bar=900, now_ts=1300,
        last_bar=None, last_side=None,
    ) is False
