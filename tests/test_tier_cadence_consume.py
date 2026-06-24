"""STAGE 1 INC3 — tier-driven cadence at the ``get_focus_targets`` consume seam.

DEMO/PAPER aggressive. flow_not_block: ALL active rows are persisted to the focus
table with a tier; a per-cycle bar-ingest read returns the rows whose tier cadence
FIRES this cycle — S/A every cycle, B every K, T every M. No row is dropped from
the table; cadence only governs HOW OFTEN it is polled. Held positions are always
force-seated regardless of tier (visibility). No cycle_index → legacy full watch.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.scripts._production_layers import get_focus_targets
from polaris.storage.schema import init_db

NOW = 1_780_000_000


@pytest.fixture()
def conn(tmp_path):  # type: ignore[no-untyped-def]
    c = init_db(tmp_path / "cadence.sqlite")
    yield c
    c.close()


def _seed_tiered_focus(conn: sqlite3.Connection, rows: list[tuple[str, str, str]]) -> None:
    """rows = [(venue, symbol, tier)] → one watchlist_focus cycle (rank by order)."""
    for i, (venue, symbol, tier) in enumerate(rows):
        conn.execute(
            "INSERT OR REPLACE INTO watchlist_focus "
            "(cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket, "
            " evict_reason, tier) VALUES (?, ?, ?, ?, ?, 'core', NULL, ?)",
            (NOW, venue, symbol, 100.0 - i, i, tier),
        )
    conn.commit()


def test_tier_column_persisted_and_read(conn: sqlite3.Connection) -> None:
    _seed_tiered_focus(conn, [("okx", "BTC-USDT", "S"), ("okx", "AAA-USDT", "T")])
    cols = {r[1] for r in conn.execute("PRAGMA table_info(watchlist_focus)").fetchall()}
    assert "tier" in cols


def test_cadence_cycle0_includes_all_tiers(conn: sqlite3.Connection) -> None:
    # Cycle 0 fires S, A, B (k|0), and T (m|0) → every tier present.
    _seed_tiered_focus(
        conn,
        [("okx", "S-USDT", "S"), ("okx", "A-USDT", "A"),
         ("okx", "B-USDT", "B"), ("okx", "T-USDT", "T")],
    )
    got = {s for _v, s, _ac, _g in get_focus_targets(conn, cycle_ts=NOW, cycle_index=0)}
    assert got == {"S-USDT", "A-USDT", "B-USDT", "T-USDT"}


def test_cadence_offcycle_drops_cool_and_tail(conn: sqlite3.Connection) -> None:
    # Cycle 1 (k=3, m=6): S/A fire, B (3∤1) + T (6∤1) do NOT → only S/A returned.
    _seed_tiered_focus(
        conn,
        [("okx", "S-USDT", "S"), ("okx", "A-USDT", "A"),
         ("okx", "B-USDT", "B"), ("okx", "T-USDT", "T")],
    )
    got = {
        s for _v, s, _ac, _g in get_focus_targets(
            conn, cycle_ts=NOW, cycle_index=1, cadence_k=3, cadence_m=6
        )
    }
    assert got == {"S-USDT", "A-USDT"}
    assert "B-USDT" not in got and "T-USDT" not in got


def test_cadence_b_cycle_includes_b_not_t(conn: sqlite3.Connection) -> None:
    # Cycle 3 (k=3, m=6): B fires (3|3), T does not (6∤3).
    _seed_tiered_focus(
        conn,
        [("okx", "S-USDT", "S"), ("okx", "B-USDT", "B"), ("okx", "T-USDT", "T")],
    )
    got = {
        s for _v, s, _ac, _g in get_focus_targets(
            conn, cycle_ts=NOW, cycle_index=3, cadence_k=3, cadence_m=6
        )
    }
    assert "B-USDT" in got
    assert "T-USDT" not in got


def test_no_cycle_index_is_full_watch(conn: sqlite3.Connection) -> None:
    # Legacy callers (held-position union, smoke) pass no cycle_index → full watch
    # (every tier returned) so back-compat + flow_not_block hold.
    _seed_tiered_focus(
        conn,
        [("okx", "S-USDT", "S"), ("okx", "B-USDT", "B"), ("okx", "T-USDT", "T")],
    )
    got = {s for _v, s, _ac, _g in get_focus_targets(conn, cycle_ts=NOW)}
    assert got == {"S-USDT", "B-USDT", "T-USDT"}


def test_held_position_seated_regardless_of_cadence(conn: sqlite3.Connection) -> None:
    # A held name in a cool/tail tier that does NOT fire this cycle is STILL
    # force-seated (held visibility wins over cadence).
    _seed_tiered_focus(conn, [("okx", "T-USDT", "T")])
    conn.execute(
        "INSERT OR REPLACE INTO universe (venue, symbol, instrument_id, "
        "underlying_group_id, asset_class, quote_ccy, state, is_active, "
        "last_seen_ts) VALUES "
        "('okx','HELD-USDT','okx:HELD-USDT','crypto:HELD','crypto','USDT','live',"
        "1,?)",
        (NOW,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO positions (position_id, venue, symbol, "
        "underlying_group_id, strategy_id, entry_strategy_id, active_strategy_id, "
        "side, qty, status, opened_ts, swap_count) VALUES "
        "('p1','okx','HELD-USDT','crypto:HELD','vb','vb','vb','long',1.0,'open',?,0)",
        (NOW,),
    )
    conn.commit()
    # Cycle 1: T does not fire, but the held name is seated anyway.
    got = {
        s for _v, s, _ac, _g in get_focus_targets(
            conn, cycle_ts=NOW, cycle_index=1, cadence_k=3, cadence_m=6
        )
    }
    assert "HELD-USDT" in got
