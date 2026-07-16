"""TDD — delegation-gate historical-edge read (P2b, SHADOW v1).

score_f_events (venue,strategy) x (symbol, via positions join) gross_bps,
small-sample-shrunk — TRADING-domain, read-only. Design SSOT:
vault/50_research/delegation-gate-blueprint.md.
"""

from __future__ import annotations

import sqlite3

from polaris.core.delegation.historical_edge import (
    HISTORY_SHRINKAGE_K,
    fetch_historical_edge_bps,
)


def _shrunk(raw_bps: float, n: int) -> float:
    """Mirrors fetch_historical_edge_bps's own shrinkage formula — tests
    assert against the formula (robust to HISTORY_SHRINKAGE_K retuning),
    not a hand-computed decimal."""
    return raw_bps * n / (n + HISTORY_SHRINKAGE_K)


def _conn() -> sqlite3.Connection:
    from polaris.storage.schema import ALL_DDL

    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    return conn


def _insert_position(conn: sqlite3.Connection, *, position_id: str, symbol: str) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status) "
        "VALUES (?, 'capital', ?, 's1', 's1', 's1', 'long', 1.0, 'closed')",
        (position_id, symbol),
    )


def _insert_score_event(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    strategy_id: str,
    gross_usd: float,
    notional_usd: float,
) -> None:
    conn.execute(
        "INSERT INTO score_f_events (position_id, venue, strategy_id, day, "
        "closed_ts, gross_usd, notional_usd) VALUES (?, 'capital', ?, '2026-07-16', "
        "1000, ?, ?)",
        (position_id, strategy_id, gross_usd, notional_usd),
    )


def test_computes_gross_bps_from_sum_ratio() -> None:
    conn = _conn()
    _insert_position(conn, position_id="p1", symbol="XAUUSD")
    _insert_score_event(
        conn, position_id="p1", strategy_id="xau_indices_trend",
        gross_usd=5.0, notional_usd=1000.0,
    )
    out = fetch_historical_edge_bps(conn, venue="capital", symbol="XAUUSD")
    # 5/1000 * 10_000 = 50 raw bps, n=1 closed lifecycle -> shrunk
    assert out["xau_indices_trend"] == _shrunk(50.0, n=1)


def test_aggregates_multiple_closed_lifecycles() -> None:
    conn = _conn()
    _insert_position(conn, position_id="p1", symbol="XAUUSD")
    _insert_position(conn, position_id="p2", symbol="XAUUSD")
    _insert_score_event(
        conn, position_id="p1", strategy_id="xau_indices_trend",
        gross_usd=5.0, notional_usd=1000.0,
    )
    _insert_score_event(
        conn, position_id="p2", strategy_id="xau_indices_trend",
        gross_usd=-15.0, notional_usd=1000.0,
    )
    out = fetch_historical_edge_bps(conn, venue="capital", symbol="XAUUSD")
    # (5 - 15) / 2000 * 10_000 = -50 raw bps, n=2 closed lifecycles -> shrunk
    assert out["xau_indices_trend"] == _shrunk(-50.0, n=2)


def test_symbol_scoped_via_positions_join() -> None:
    conn = _conn()
    _insert_position(conn, position_id="p1", symbol="XAUUSD")
    _insert_position(conn, position_id="p2", symbol="EURUSD")
    _insert_score_event(
        conn, position_id="p1", strategy_id="s1", gross_usd=5.0, notional_usd=1000.0,
    )
    _insert_score_event(
        conn, position_id="p2", strategy_id="s1", gross_usd=99.0, notional_usd=1000.0,
    )
    out = fetch_historical_edge_bps(conn, venue="capital", symbol="XAUUSD")
    assert out["s1"] == _shrunk(50.0, n=1)  # only p1's row is counted


def test_more_samples_shrinks_less_same_raw_bps() -> None:
    # A thin sample must contribute LESS of its raw magnitude than a deep
    # one — the concrete failure scenario this shrinkage fixes (a single
    # +300bps trade saturating fit_score's history term like 1000 trades
    # averaging +300bps).
    conn = _conn()
    _insert_position(conn, position_id="p1", symbol="XAUUSD")
    _insert_score_event(
        conn, position_id="p1", strategy_id="thin", gross_usd=30.0, notional_usd=1000.0,
    )
    thin_out = fetch_historical_edge_bps(conn, venue="capital", symbol="XAUUSD")

    conn2 = _conn()
    for i in range(30):
        pid = f"p{i}"
        _insert_position(conn2, position_id=pid, symbol="XAUUSD")
        _insert_score_event(
            conn2, position_id=pid, strategy_id="deep",
            gross_usd=30.0, notional_usd=1000.0,
        )
    deep_out = fetch_historical_edge_bps(conn2, venue="capital", symbol="XAUUSD")

    # Same raw 300bps/leg average, but n=1 shrinks far more than n=30.
    assert thin_out["thin"] < deep_out["deep"]
    assert deep_out["deep"] < 300.0  # still shrunk below the raw magnitude


def test_missing_notional_omits_strategy() -> None:
    conn = _conn()
    _insert_position(conn, position_id="p1", symbol="XAUUSD")
    conn.execute(
        "INSERT INTO score_f_events (position_id, venue, strategy_id, day, "
        "closed_ts) VALUES ('p1', 'capital', 's1', '2026-07-16', 1000)"
    )
    out = fetch_historical_edge_bps(conn, venue="capital", symbol="XAUUSD")
    assert "s1" not in out


def test_no_data_returns_empty_dict() -> None:
    conn = _conn()
    assert fetch_historical_edge_bps(conn, venue="capital", symbol="NOPE") == {}


def test_query_fault_degrades_to_empty_dict() -> None:
    conn = sqlite3.connect(":memory:")  # no tables -> query raises
    assert fetch_historical_edge_bps(conn, venue="capital", symbol="X") == {}
