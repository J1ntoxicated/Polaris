"""P1 re-entry cooldown — over-trading / turnover-cost guard tests.

DEMO/PAPER. The guard removes *duplicate* opens on the same
(venue, symbol, strategy_id) inside a short window; it never shrinks size,
never halts on P&L, and exempts strong signals (AGGRESSIVE flow preserved).
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import uuid
from collections.abc import Iterator
from types import ModuleType
from typing import Literal

import pytest

from polaris.core.isolation.reentry import (
    REENTRY_COOLDOWN_SEC,
    bar_seconds,
    concurrent_same_side_open,
    is_novel_reentry,
    reentry_cooldown_active,
    stamp_reentry_anchor,
)
from polaris.core.lifecycle.recover import hydrate_last_entry_by_key
from polaris.storage.schema import ALL_DDL

_VIRTUAL_ENV = "POLARIS_VIRTUAL_ACCOUNT"


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
    pnl_r: float | None = None,
) -> None:
    conn.execute(
        "INSERT INTO positions ("
        "position_id, venue, symbol, underlying_group_id, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts, "
        "pnl_r"
        ") VALUES (?, ?, ?, '', ?, ?, ?, ?, 1.0, ?, ?, ?)",
        (
            uuid.uuid4().hex, venue, symbol, strategy_id,
            strategy_id, strategy_id, side, status, opened_ts, pnl_r,
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


# --- Reject-anchor anti-churn (audit1 P0-4 ①) -------------------------------
# A venue reject/clamp never writes a ``positions`` row, so the cooldown had no
# anchor to check against (PANW: 58 intents/6.1h — every reject reset the
# window to zero even though the novelty stamp correctly flagged "not novel").
# ``stamp_reentry_anchor`` gives the cooldown a persistent anchor that survives
# a reject and a process restart.


def test_reject_anchor_blocks_without_positions_row(
    conn: sqlite3.Connection,
) -> None:
    # NO positions row exists (every attempt on this key was rejected/clamped) —
    # only the persisted reject-anchor. The cooldown must still engage.
    stamp_reentry_anchor(
        conn, venue="alpaca", symbol="PANW", strategy_id="equity_52wk_high_breakout",
        now_ts=1000,
    )
    assert reentry_cooldown_active(
        conn, venue="alpaca", symbol="PANW",
        strategy_id="equity_52wk_high_breakout",
        now_ts=1040, cooldown_sec=300, exempt=False,
    ) is True


def test_reject_anchor_expires_after_window(conn: sqlite3.Connection) -> None:
    stamp_reentry_anchor(
        conn, venue="alpaca", symbol="PANW", strategy_id="equity_52wk_high_breakout",
        now_ts=1000,
    )
    assert reentry_cooldown_active(
        conn, venue="alpaca", symbol="PANW",
        strategy_id="equity_52wk_high_breakout",
        now_ts=1000 + 300, cooldown_sec=300, exempt=False,
    ) is False


def test_reject_anchor_repeated_stamp_advances_window(
    conn: sqlite3.Connection,
) -> None:
    # Each repeated reject re-stamps last_ts (UPSERT), so the window keeps
    # sliding forward exactly like a repeated fill would.
    stamp_reentry_anchor(
        conn, venue="alpaca", symbol="PANW", strategy_id="equity_52wk_high_breakout",
        now_ts=1000,
    )
    stamp_reentry_anchor(
        conn, venue="alpaca", symbol="PANW", strategy_id="equity_52wk_high_breakout",
        now_ts=1200,
    )
    assert reentry_cooldown_active(
        conn, venue="alpaca", symbol="PANW",
        strategy_id="equity_52wk_high_breakout",
        now_ts=1499, cooldown_sec=300, exempt=False,
    ) is True
    assert reentry_cooldown_active(
        conn, venue="alpaca", symbol="PANW",
        strategy_id="equity_52wk_high_breakout",
        now_ts=1500, cooldown_sec=300, exempt=False,
    ) is False


def test_reject_anchor_exempt_on_novelty_still_allows(
    conn: sqlite3.Connection,
) -> None:
    # FLOW: novelty (new bar / side flip) still exempts even with a live
    # reject-anchor — the anchor tightens the WINDOW check only, it never
    # overrides the novelty exemption (flow_not_block).
    stamp_reentry_anchor(
        conn, venue="alpaca", symbol="PANW", strategy_id="equity_52wk_high_breakout",
        now_ts=1000,
    )
    assert reentry_cooldown_active(
        conn, venue="alpaca", symbol="PANW",
        strategy_id="equity_52wk_high_breakout",
        now_ts=1040, cooldown_sec=300, exempt=True,
    ) is False


def test_reject_anchor_key_isolation(conn: sqlite3.Connection) -> None:
    stamp_reentry_anchor(
        conn, venue="alpaca", symbol="PANW", strategy_id="equity_52wk_high_breakout",
        now_ts=1000,
    )
    # Different symbol / strategy / venue keys are unaffected.
    assert reentry_cooldown_active(
        conn, venue="alpaca", symbol="ABBV",
        strategy_id="equity_52wk_high_breakout",
        now_ts=1010, cooldown_sec=300, exempt=False,
    ) is False
    assert reentry_cooldown_active(
        conn, venue="alpaca", symbol="PANW", strategy_id="tsmom",
        now_ts=1010, cooldown_sec=300, exempt=False,
    ) is False


def test_reject_anchor_combines_with_positions_row_max(
    conn: sqlite3.Connection,
) -> None:
    # An OLDER reject-anchor must not shadow a MORE RECENT positions open (the
    # cooldown check uses the max of the two anchors). The reject anchor alone
    # (ts=1000) would have expired by now_ts=1300 (300s window), but the
    # positions row (ts=1250) is still within ITS 300s window at ts=1300.
    stamp_reentry_anchor(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        now_ts=1000,
    )
    _insert_position(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        opened_ts=1250,
    )
    assert reentry_cooldown_active(
        conn, venue="okx", symbol="SOL-USDT", strategy_id="volume_burst",
        now_ts=1300, cooldown_sec=300, exempt=False,
    ) is True


def test_reject_anchor_disabled_when_cooldown_zero(
    conn: sqlite3.Connection,
) -> None:
    stamp_reentry_anchor(
        conn, venue="alpaca", symbol="PANW", strategy_id="equity_52wk_high_breakout",
        now_ts=1000,
    )
    assert reentry_cooldown_active(
        conn, venue="alpaca", symbol="PANW",
        strategy_id="equity_52wk_high_breakout",
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


# --- P0-2 boot-refire fix: hydrate_last_entry_by_key restores the anchor ----
#
# Forensic: state.last_entry_by_key is in-memory only, so a paper-loop
# restart wiped it — last_entry_bar=None made is_novel_reentry always True,
# which exempted the cooldown unconditionally, refiring the SAME signal
# within seconds of every boot (3x observed, incl. daily 07:30 restart).
# These tests drive the exact entry seam composition through a simulated
# boot (open → close → restart → hydrate) to prove the anchor is restored.


def test_boot_refire_same_1d_bar_same_side_skipped_after_hydrate(
    conn: sqlite3.Connection,
) -> None:
    # Open then close a 1D-bar position (gold_trend_chandelier_1d shape).
    _insert_position(
        conn, venue="capital", symbol="XAUUSD",
        strategy_id="gold_trend_chandelier_1d", opened_ts=100_000,
        status="closed", side="long",
    )
    # Simulate a process restart: rebuild the anchor purely from SQLite (no
    # in-memory state survives), exactly as production_paper_loop's boot
    # sequence does.
    restored = hydrate_last_entry_by_key(conn)
    last_bar, last_side = restored[("capital", "XAUUSD", "gold_trend_chandelier_1d")]
    assert (last_bar, last_side) == (100_000, "long")
    # A same-1D-bar, same-side signal fires 48s after the restart (the
    # observed J225/AU200AU shape) — WITHOUT the fix last_bar would be None
    # (always novel) and this would flow straight through; WITH the fix the
    # bar id (100_000, unchanged — no new 1D bar yet) + same side is NOT
    # novel → the cooldown applies → skipped.
    assert _seam_skips(
        conn, venue="capital", symbol="XAUUSD",
        strategy_id="gold_trend_chandelier_1d", timeframe="1D", side="long",
        created_at_bar=100_000, now_ts=100_048,
        last_bar=last_bar, last_side=last_side,
    ) is True


def test_boot_refire_new_bar_after_restart_still_flows(
    conn: sqlite3.Connection,
) -> None:
    # Same boot simulation, but the signal now carries a genuinely NEW
    # 1D-bar id (created_at_bar advanced) — novelty exemption must still
    # flow (flow_not_block preserved by the fix, not a new block).
    _insert_position(
        conn, venue="capital", symbol="XAUUSD",
        strategy_id="gold_trend_chandelier_1d", opened_ts=100_000,
        status="closed", side="long",
    )
    restored = hydrate_last_entry_by_key(conn)
    last_bar, last_side = restored[("capital", "XAUUSD", "gold_trend_chandelier_1d")]
    assert _seam_skips(
        conn, venue="capital", symbol="XAUUSD",
        strategy_id="gold_trend_chandelier_1d", timeframe="1D", side="long",
        created_at_bar=186_400, now_ts=200_000,  # a full day later, new bar
        last_bar=last_bar, last_side=last_side,
    ) is False


def test_boot_refire_side_flip_after_restart_still_flows(
    conn: sqlite3.Connection,
) -> None:
    # Same boot simulation, but the signal flips side within the same bar —
    # the thesis reversed, so novelty must still exempt it.
    _insert_position(
        conn, venue="capital", symbol="AU200AU",
        strategy_id="xau_indices_trend", opened_ts=100_000,
        status="closed", side="long",
    )
    restored = hydrate_last_entry_by_key(conn)
    last_bar, last_side = restored[("capital", "AU200AU", "xau_indices_trend")]
    assert _seam_skips(
        conn, venue="capital", symbol="AU200AU", strategy_id="xau_indices_trend",
        timeframe="1D", side="short", created_at_bar=100_000, now_ts=100_048,
        last_bar=last_bar, last_side=last_side,
    ) is False


def test_boot_refire_no_prior_position_still_first_entry_allowed(
    conn: sqlite3.Connection,
) -> None:
    # A key with NO persisted positions row (genuinely first-ever entry) must
    # still be allowed post-restart — hydrate returning an empty dict is not
    # itself a block.
    restored = hydrate_last_entry_by_key(conn)
    assert ("okx", "NEW-USDT", "tsmom") not in restored
    assert _seam_skips(
        conn, venue="okx", symbol="NEW-USDT", strategy_id="tsmom",
        timeframe="1H", side="long", created_at_bar=900, now_ts=1300,
        last_bar=None, last_side=None,
    ) is False


# --- VIRTUAL-mode re-entry cooldown loosening v2 (Jin 2026-07-09) -----------
#
# Surgical redo of the REJECTed v1 (commit 9d4cd57 on
# virtual/reentry-rotation-cooldown-loosen): v1 scaled ``bar_seconds()``
# itself, a SHARED physical-bar primitive also consumed by
# ``exit_params.hold_frac_for_timeframe`` (1D/intraday classification),
# ``loser_timeout`` (drift-backstop floor + 4H cap-exempt boundary), and
# ``_production_recalc`` (maturity-gate horizon) — none of which are the
# re-entry cooldown. Scaling it silently mis-classified a virtual 1D position
# as intraday (43200s < the 86400s daily threshold) = an exit regression. v2
# introduces ``reentry_cooldown_seconds`` as a SEPARATE virtual-scaled seam;
# ``bar_seconds`` itself reads no env at all in either mode. Module-level
# ``_COOLDOWN_FACTOR`` is read once at import, so it is exercised via
# ``importlib.reload`` (mirrors ``test_virtual_loosen_okx_donchian55.py``'s
# established idiom); ``bar_seconds`` / downstream consumers need no reload
# since they take no env-dependent shortcuts.


def _reload_reentry_with_env(value: str | None) -> ModuleType:
    if value is None:
        os.environ.pop(_VIRTUAL_ENV, None)
    else:
        os.environ[_VIRTUAL_ENV] = value
    import polaris.core.isolation.reentry as reentry_mod

    return importlib.reload(reentry_mod)


@pytest.fixture(autouse=True)
def _restore_virtual_env_and_module() -> Iterator[None]:
    """Isolate the env var + force a fresh import per test (no cross-test leak)."""
    prior = os.environ.get(_VIRTUAL_ENV)
    yield
    if prior is None:
        os.environ.pop(_VIRTUAL_ENV, None)
    else:
        os.environ[_VIRTUAL_ENV] = prior
    import polaris.core.isolation.reentry as reentry_mod

    os.environ.pop(_VIRTUAL_ENV, None)
    importlib.reload(reentry_mod)


_ALL_TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H", "1D", "bogus")


def test_bar_seconds_is_env_invariant_real_and_virtual() -> None:
    # bar_seconds() is a PURE physical-bar primitive — zero env coupling in
    # EITHER mode. This is the structural fix for the REJECT: v1 made it
    # env-branching, which silently broke the three other consumers.
    real_mod = _reload_reentry_with_env(None)
    real_values = {tf: real_mod.bar_seconds(tf) for tf in _ALL_TIMEFRAMES}
    virtual_mod = _reload_reentry_with_env("1")
    virtual_values = {tf: virtual_mod.bar_seconds(tf) for tf in _ALL_TIMEFRAMES}
    assert real_values == virtual_values
    assert real_values["1D"] == 86400  # daily-classification boundary, locked.
    assert real_values["4H"] == 14400  # loser_timeout cap-exempt boundary, locked.


def test_cooldown_factor_real_reentry_cooldown_seconds_matches_bar_seconds() -> None:
    real_mod = _reload_reentry_with_env(None)
    assert real_mod._COOLDOWN_FACTOR == 1.0
    for tf in _ALL_TIMEFRAMES:
        assert real_mod.reentry_cooldown_seconds(tf) == real_mod.bar_seconds(tf)


# --- concurrent_same_side_open skip-pressure loosening v3 (Jin 2026-07-09) --
#
# Concurrent_same_side_open skip pressure (676 skips/day observed): the flat
# 0.5x cooldown-window factor + the uniform 2-ceiling tailored cap still left
# a lot of virtual-mode dead air, since virtual has no real capital/fee cost
# to protect against. Two INDEPENDENT SLOT-COUNT-only knobs widen further:
#   1. POLARIS_VIRTUAL_COOLDOWN_FACTOR — default 0.25x (was hardcoded 0.5x),
#      env-overridable; REAL's 1.0x branch never reads this env var.
#   2. TAILORED_CAP_CEILING — 3 in VIRTUAL (was uniform 2), reusing the
#      existing ``_VIRTUAL`` flag; REAL stays byte-identical at 2.
# Neither touches per-symbol/cluster notional caps, the -1R rail, the sizing
# multiplier chain, or the CS3_N_THRESHOLD / win-rate sample gates below.

_COOLDOWN_FACTOR_ENV = "POLARIS_VIRTUAL_COOLDOWN_FACTOR"


def test_cooldown_factor_virtual_defaults_to_quarter_reentry_cooldown_seconds_only() -> (
    None
):
    virtual_mod = _reload_reentry_with_env("1")
    assert virtual_mod._COOLDOWN_FACTOR == 0.25
    assert virtual_mod.reentry_cooldown_seconds("1H") == 900
    assert virtual_mod.reentry_cooldown_seconds("1m") == 15
    assert virtual_mod.reentry_cooldown_seconds("5m") == 75
    assert virtual_mod.reentry_cooldown_seconds("15m") == 225
    assert virtual_mod.reentry_cooldown_seconds("4H") == 3600
    assert virtual_mod.reentry_cooldown_seconds("1D") == 21600
    assert virtual_mod.reentry_cooldown_seconds("bogus") == 75  # unknown also quarters.
    # bar_seconds itself is UNCHANGED — the exact regression the earlier REJECT
    # flagged (v1 scaled bar_seconds directly and broke three other consumers).
    assert virtual_mod.bar_seconds("1D") == 86400
    assert virtual_mod.bar_seconds("4H") == 14400


def test_cooldown_factor_real_ignores_virtual_factor_env_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # REAL must stay byte-identical to bar_seconds even if the VIRTUAL-only
    # factor env is (mis)set — the ``else 1.0`` branch never consults it.
    monkeypatch.setenv(_COOLDOWN_FACTOR_ENV, "0.01")
    real_mod = _reload_reentry_with_env(None)
    assert real_mod._COOLDOWN_FACTOR == 1.0
    for tf in _ALL_TIMEFRAMES:
        assert real_mod.reentry_cooldown_seconds(tf) == real_mod.bar_seconds(tf)


def test_cooldown_factor_virtual_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_COOLDOWN_FACTOR_ENV, "0.1")
    virtual_mod = _reload_reentry_with_env("1")
    assert pytest.approx(0.1) == virtual_mod._COOLDOWN_FACTOR
    assert virtual_mod.reentry_cooldown_seconds("1H") == 360


def test_win_rate_floor_and_cs3_threshold_unaffected_by_virtual_mode() -> None:
    # Regression: the slot-count loosening must NOT touch the win-rate floor
    # or the Cold-Start CS-3 sample-floor rail (non-degenerate gate preserved,
    # notional caps/sizing untouched, per spec).
    real_mod = _reload_reentry_with_env(None)
    real_win_rate_floor = real_mod.TAILORED_CAP_WIN_RATE_FLOOR
    real_cs3 = real_mod.CS3_N_THRESHOLD

    virtual_mod = _reload_reentry_with_env("1")
    assert real_win_rate_floor == virtual_mod.TAILORED_CAP_WIN_RATE_FLOOR
    assert real_cs3 == virtual_mod.CS3_N_THRESHOLD


def test_tailored_cap_ceiling_virtual_widens_real_stays_byte_identical() -> None:
    # The SLOT-COUNT widen: REAL keeps the pre-existing ceiling of 2
    # (byte-identical — untouched); VIRTUAL widens to 3 (2026-07-09 slot-cap
    # loosening, reusing the existing ``_VIRTUAL`` flag).
    real_mod = _reload_reentry_with_env(None)
    assert real_mod.TAILORED_CAP_CEILING == 2
    virtual_mod = _reload_reentry_with_env("1")
    assert virtual_mod.TAILORED_CAP_CEILING == 3


def test_seam_skip_count_decreases_for_proven_edge_key_in_virtual_mode(
    conn: sqlite3.Connection,
) -> None:
    # Concrete before/after on the SAME proven-edge key + 2 already-live
    # same-side positions: REAL (ceiling=2) still SKIPS a 3rd entry; VIRTUAL
    # (ceiling=3) ALLOWS it — the concurrent_same_side_open skip for this
    # key/slot disappears. Only the slot COUNT moved; notional/sizing untouched.
    real_mod = _reload_reentry_with_env(None)
    for _ in range(real_mod.CS3_N_THRESHOLD):
        _insert_position(
            conn, venue="okx", symbol="SOL-USDT",
            strategy_id="donchian_turtle_breakout", opened_ts=1000,
            status="closed", pnl_r=1.0,  # 100% win-rate, n >= CS3_N_THRESHOLD.
        )
    _insert_position(
        conn, venue="okx", symbol="SOL-USDT",
        strategy_id="donchian_turtle_breakout", opened_ts=2000, status="open",
    )
    _insert_position(
        conn, venue="okx", symbol="SOL-USDT",
        strategy_id="donchian_turtle_breakout", opened_ts=2001, status="open",
    )

    real_cap = real_mod.tailored_concurrent_cap(
        conn, venue="okx", symbol="SOL-USDT",
        strategy_id="donchian_turtle_breakout",
    )
    assert real_cap == 2
    assert real_mod.concurrent_same_side_open(
        conn, venue="okx", symbol="SOL-USDT",
        strategy_id="donchian_turtle_breakout", side="long", cap=real_cap,
    ) is True  # REAL: 2 live AT the cap(2) -> 3rd entry SKIPPED.

    virtual_mod = _reload_reentry_with_env("1")
    virtual_cap = virtual_mod.tailored_concurrent_cap(
        conn, venue="okx", symbol="SOL-USDT",
        strategy_id="donchian_turtle_breakout",
    )
    assert virtual_cap == 3
    assert virtual_mod.concurrent_same_side_open(
        conn, venue="okx", symbol="SOL-USDT",
        strategy_id="donchian_turtle_breakout", side="long", cap=virtual_cap,
    ) is False  # VIRTUAL: 2 live UNDER the widened cap(3) -> 3rd ALLOWED.


def test_virtual_mode_thin_sample_still_caps_at_one_and_still_skips(
    conn: sqlite3.Connection,
) -> None:
    # Non-degenerate boundary regression: n < CS3_N_THRESHOLD (all wins) must
    # still cap at 1 EVEN in VIRTUAL mode — the widened ceiling never bypasses
    # the sample-size gate.
    virtual_mod = _reload_reentry_with_env("1")
    for _ in range(virtual_mod.CS3_N_THRESHOLD - 1):
        _insert_position(
            conn, venue="okx", symbol="ADA-USDT", strategy_id="tsmom",
            opened_ts=1000, status="closed", pnl_r=1.0,
        )
    assert virtual_mod.tailored_concurrent_cap(
        conn, venue="okx", symbol="ADA-USDT", strategy_id="tsmom",
    ) == 1
    _insert_position(
        conn, venue="okx", symbol="ADA-USDT", strategy_id="tsmom",
        opened_ts=2000, status="open",
    )
    assert virtual_mod.concurrent_same_side_open(
        conn, venue="okx", symbol="ADA-USDT", strategy_id="tsmom", side="long",
        cap=1,
    ) is True  # still SKIPPED — thin sample earns no extra slot.


def test_virtual_mode_weak_edge_still_caps_at_one_and_still_skips(
    conn: sqlite3.Connection,
) -> None:
    # Non-degenerate boundary regression: n >= CS3_N_THRESHOLD but win-rate
    # BELOW TAILORED_CAP_WIN_RATE_FLOOR must still cap at 1 EVEN in VIRTUAL
    # mode — a saturated/weak-edge name gets no extra room in either mode.
    virtual_mod = _reload_reentry_with_env("1")
    for _ in range(virtual_mod.CS3_N_THRESHOLD):
        _insert_position(
            conn, venue="okx", symbol="DOGE-USDT", strategy_id="tsmom",
            opened_ts=1000, status="closed", pnl_r=-1.0,
        )
    assert virtual_mod.tailored_concurrent_cap(
        conn, venue="okx", symbol="DOGE-USDT", strategy_id="tsmom",
    ) == 1
    _insert_position(
        conn, venue="okx", symbol="DOGE-USDT", strategy_id="tsmom",
        opened_ts=2000, status="open",
    )
    assert virtual_mod.concurrent_same_side_open(
        conn, venue="okx", symbol="DOGE-USDT", strategy_id="tsmom", side="long",
        cap=1,
    ) is True  # still SKIPPED — weak edge earns no extra slot.


def test_hold_frac_for_timeframe_1d_unaffected_by_virtual_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # REJECT root-cause regression guard: v1 scaled bar_seconds() itself, so in
    # virtual mode bar_seconds("1D")==43200 fell BELOW the 86400s daily
    # threshold in exit_params.hold_frac_for_timeframe -> a virtual 1D
    # position was misclassified as intraday (hold_frac 0.05 instead of 0.10),
    # an exit-timing regression. v2's reentry_cooldown_seconds seam does not
    # touch bar_seconds, so this must hold identically in EITHER mode.
    from polaris.core.live_recalc.exit_engine import hold_frac_for_timeframe

    monkeypatch.delenv(_VIRTUAL_ENV, raising=False)
    assert hold_frac_for_timeframe("1D") == pytest.approx(0.10)
    assert hold_frac_for_timeframe("1H") == pytest.approx(0.05)

    monkeypatch.setenv(_VIRTUAL_ENV, "1")
    assert hold_frac_for_timeframe("1D") == pytest.approx(0.10)
    assert hold_frac_for_timeframe("1H") == pytest.approx(0.05)


def test_loser_timeout_floor_unaffected_by_virtual_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression guard: loser_timeout's bar-scaled floor + 4H cap-exempt
    # boundary both depend on bar_seconds() — confirm byte-identical results
    # across REAL/VIRTUAL now that bar_seconds() itself is env-invariant.
    from polaris.core.live_recalc.loser_timeout import loser_timeout_for_strategy

    monkeypatch.delenv(_VIRTUAL_ENV, raising=False)
    real_daily = loser_timeout_for_strategy("connors_rsi2")
    real_1h = loser_timeout_for_strategy("ema_crossover")

    monkeypatch.setenv(_VIRTUAL_ENV, "1")
    assert loser_timeout_for_strategy("connors_rsi2") == real_daily
    assert loser_timeout_for_strategy("ema_crossover") == real_1h
