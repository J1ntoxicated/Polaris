"""FIX 2/2 — HELD-position symbols are ALWAYS in the live (focus∪held) set.

Root: the WS subscription + per-tick bar ingest both read ``get_focus_targets``.
A HELD position whose symbol is NOT in ``compute_dynamic_focus`` (e.g.
``okx:HYPE-USDT``, ``alpaca:SPCE``) got NO quote tick + NO fresh bars → the
dashboard froze on a stale bar and the exit engine ran on a stale price.

Contract (this fix):
- ``open_position_targets`` reads every OPEN position as a focus-shaped tuple
  ``(venue, symbol, asset_class, group_id)``, across ALL venues.
- ``get_focus_targets`` returns ``dynamic_focus ∪ {open-position symbols}`` —
  the held symbols are appended AFTER the dynamic picks (additive) and are NOT
  truncated by ``max_n`` (a held name can never fall off while it is held).
- A held symbol drops from the set only once its position closes.
- Entries/focus behaviour is otherwise unchanged: the dynamic picks are still
  present and still come first.

Visibility/precision, NOT a throttle — we ADD symbols to watch, never block an
entry (flow_not_block intact). DEMO/PAPER only.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.scripts._production_layers import (
    get_focus_targets,
    open_position_targets,
)
from polaris.storage.schema import init_db

NOW = 1_780_000_000


@pytest.fixture()
def conn(tmp_path):
    c = init_db(tmp_path / "held_focus.sqlite")
    yield c
    c.close()


def _seed_focus(conn: sqlite3.Connection, rows: list[tuple[str, str]]) -> None:
    """rows = [(venue, symbol)] → one watchlist_focus cycle."""
    cycle = NOW
    for i, (venue, symbol) in enumerate(rows):
        conn.execute(
            "INSERT OR REPLACE INTO watchlist_focus "
            "(cycle_ts, venue, symbol, focus_score, focus_rank, target_bucket, "
            " evict_reason) VALUES (?, ?, ?, ?, ?, 'core', NULL)",
            (cycle, venue, symbol, 100.0 - i, i),
        )
    conn.commit()


def _seed_universe(
    conn: sqlite3.Connection, venue: str, symbol: str, *,
    asset_class: str, group_id: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO universe "
        "(venue, symbol, instrument_id, underlying_group_id, asset_class, "
        " quote_ccy, state, is_active, last_seen_ts) "
        "VALUES (?, ?, ?, ?, ?, 'USDT', 'live', 1, ?)",
        (venue, symbol, f"{venue}:{symbol}", group_id, asset_class, NOW),
    )
    conn.commit()


def _seed_open_position(
    conn: sqlite3.Connection, *, position_id: str, venue: str, symbol: str,
    group_id: str = "", status: str = "open",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO positions "
        "(position_id, venue, symbol, underlying_group_id, strategy_id, "
        " entry_strategy_id, active_strategy_id, side, qty, status, "
        " opened_ts, swap_count) "
        "VALUES (?, ?, ?, ?, 'vb', 'vb', 'vb', 'long', 1.0, ?, ?, 0)",
        (position_id, venue, symbol, group_id, status, NOW),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# open_position_targets — focus-shaped read of every OPEN position
# ---------------------------------------------------------------------------


def test_open_position_targets_returns_focus_shaped_tuples(conn) -> None:
    _seed_universe(conn, "okx", "HYPE-USDT", asset_class="crypto", group_id="crypto:HYPE")
    _seed_open_position(
        conn, position_id="p1", venue="okx", symbol="HYPE-USDT", group_id="crypto:HYPE"
    )
    targets = open_position_targets(conn)
    assert ("okx", "HYPE-USDT", "crypto", "crypto:HYPE") in targets


def test_open_position_targets_all_venues(conn) -> None:
    """OKX + Capital + Alpaca held positions are ALL returned (every venue)."""
    _seed_open_position(
        conn, position_id="p1", venue="okx", symbol="HYPE-USDT", group_id="crypto:HYPE"
    )
    _seed_open_position(
        conn, position_id="p2", venue="capital", symbol="CS.D.GBPUSD.CFD.IP",
        group_id="forex:GBPUSD",
    )
    _seed_open_position(
        conn, position_id="p3", venue="alpaca", symbol="SPCE", group_id="equity:SPCE"
    )
    venues = {t[0] for t in open_position_targets(conn)}
    assert venues == {"okx", "capital", "alpaca"}


def test_open_position_targets_excludes_closed(conn) -> None:
    _seed_open_position(
        conn, position_id="p1", venue="okx", symbol="HYPE-USDT",
        group_id="crypto:HYPE", status="closed",
    )
    _seed_open_position(
        conn, position_id="p2", venue="okx", symbol="BCH-USDT",
        group_id="crypto:BCH", status="reconciled",
    )
    assert open_position_targets(conn) == []


def test_open_position_targets_dedups_same_symbol(conn) -> None:
    """Two open positions on the same (venue, symbol) → one target."""
    _seed_open_position(
        conn, position_id="p1", venue="alpaca", symbol="SPCE", group_id="equity:SPCE"
    )
    _seed_open_position(
        conn, position_id="p2", venue="alpaca", symbol="SPCE", group_id="equity:SPCE"
    )
    targets = open_position_targets(conn)
    assert targets.count(("alpaca", "SPCE", "equity", "equity:SPCE")) == 1


# ---------------------------------------------------------------------------
# get_focus_targets — union with open positions
# ---------------------------------------------------------------------------


def test_focus_union_includes_held_symbol_not_in_focus(conn) -> None:
    """A held symbol absent from the dynamic focus is STILL in the resolved set."""
    _seed_focus(conn, [("okx", "BTC-USDT"), ("okx", "ETH-USDT")])
    _seed_universe(conn, "okx", "HYPE-USDT", asset_class="crypto", group_id="crypto:HYPE")
    _seed_open_position(
        conn, position_id="p1", venue="okx", symbol="HYPE-USDT", group_id="crypto:HYPE"
    )
    targets = get_focus_targets(conn, cycle_ts=NOW, max_n=30)
    syms = {(v, s) for v, s, _ac, _g in targets}
    assert ("okx", "HYPE-USDT") in syms  # held → forced into the set
    # Dynamic picks are still present (entries/focus unchanged).
    assert ("okx", "BTC-USDT") in syms
    assert ("okx", "ETH-USDT") in syms


def test_focus_union_held_survives_max_n_truncation(conn) -> None:
    """A held symbol is NOT dropped when ``max_n`` truncates the dynamic focus."""
    _seed_focus(conn, [("okx", f"SYM{i}-USDT") for i in range(5)])
    _seed_universe(conn, "alpaca", "SPCE", asset_class="equity", group_id="equity:SPCE")
    _seed_open_position(
        conn, position_id="p1", venue="alpaca", symbol="SPCE", group_id="equity:SPCE"
    )
    # max_n smaller than the focus list → SPCE would never be a dynamic pick.
    targets = get_focus_targets(conn, cycle_ts=NOW, max_n=2)
    syms = {(v, s) for v, s, _ac, _g in targets}
    assert ("alpaca", "SPCE") in syms
    # The dynamic cut is still honoured for the focus picks themselves.
    dyn = [(v, s) for v, s, _ac, _g in targets if v == "okx"]
    assert len(dyn) == 2


def test_focus_union_drops_held_after_close(conn) -> None:
    """Once the position closes the held symbol is no longer forced in."""
    _seed_focus(conn, [("okx", "BTC-USDT")])
    _seed_universe(conn, "okx", "HYPE-USDT", asset_class="crypto", group_id="crypto:HYPE")
    _seed_open_position(
        conn, position_id="p1", venue="okx", symbol="HYPE-USDT", group_id="crypto:HYPE"
    )
    before = {(v, s) for v, s, _ac, _g in get_focus_targets(conn, cycle_ts=NOW)}
    assert ("okx", "HYPE-USDT") in before
    conn.execute("UPDATE positions SET status = 'closed' WHERE position_id = 'p1'")
    conn.commit()
    after = {(v, s) for v, s, _ac, _g in get_focus_targets(conn, cycle_ts=NOW)}
    assert ("okx", "HYPE-USDT") not in after
    assert ("okx", "BTC-USDT") in after  # focus pick unchanged


def test_focus_union_no_duplicate_when_held_also_in_focus(conn) -> None:
    """A held symbol that IS also a dynamic pick appears exactly once."""
    _seed_focus(conn, [("okx", "BTC-USDT")])
    _seed_universe(conn, "okx", "BTC-USDT", asset_class="crypto", group_id="crypto:BTC")
    _seed_open_position(
        conn, position_id="p1", venue="okx", symbol="BTC-USDT", group_id="crypto:BTC"
    )
    targets = get_focus_targets(conn, cycle_ts=NOW)
    btc = [(v, s) for v, s, _ac, _g in targets if (v, s) == ("okx", "BTC-USDT")]
    assert len(btc) == 1


def test_focus_union_no_positions_is_plain_focus(conn) -> None:
    """No open positions → byte-identical to the plain dynamic focus."""
    _seed_focus(conn, [("okx", "BTC-USDT"), ("okx", "ETH-USDT")])
    targets = get_focus_targets(conn, cycle_ts=NOW)
    assert {(v, s) for v, s, _ac, _g in targets} == {
        ("okx", "BTC-USDT"), ("okx", "ETH-USDT")
    }
