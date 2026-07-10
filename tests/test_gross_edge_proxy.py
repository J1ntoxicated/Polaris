"""Tests for gross_edge_proxy (fee-split v0 — percentile-map over the
existing score_f_events population). DEMO/PAPER only. Spec:
vault/50_research/debates/fee_split_judgment_2026-07-10.md.
"""
from __future__ import annotations

import sqlite3

import pytest

from polaris.core.classes.gross_edge_proxy import compute_gross_edge_proxy
from polaris.storage.schema import init_db


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "t.sqlite")


def _insert_event(
    conn: sqlite3.Connection, *, position_id: str, venue: str, strategy_id: str,
    closed_ts: int, net_usd: float,
) -> None:
    conn.execute(
        "INSERT INTO score_f_events (position_id, venue, strategy_id, day, "
        "closed_ts, net_usd, fee_denom_usd, score_contrib) "
        "VALUES (?, ?, ?, '2026-07-10', ?, ?, 1.0, 0.0)",
        (position_id, venue, strategy_id, closed_ts, net_usd),
    )


def test_empty_history_returns_empty_list(conn):
    assert compute_gross_edge_proxy(conn, venue="okx", strategy_id="none") == []


def test_single_row_percentile_is_one(conn):
    """A lone historical close is at-or-below itself -> percentile 1.0
    (the single-element ECDF edge case)."""
    _insert_event(conn, position_id="p1", venue="okx", strategy_id="s1", closed_ts=100, net_usd=42.0)
    samples = compute_gross_edge_proxy(conn, venue="okx", strategy_id="s1")
    assert len(samples) == 1
    assert samples[0].value == pytest.approx(1.0)


def test_proxy_values_are_ecdf_percentiles_in_unit_range(conn):
    for i, net in enumerate([-50.0, 10.0, 20.0, -5.0, 100.0]):
        _insert_event(
            conn, position_id=f"p{i}", venue="okx", strategy_id="s1",
            closed_ts=1_700_000_000 + i, net_usd=net,
        )
    samples = compute_gross_edge_proxy(conn, venue="okx", strategy_id="s1")
    assert len(samples) == 5
    for s in samples:
        assert 0.0 <= s.value <= 1.0


def test_proxy_ordering_matches_net_usd_rank(conn):
    """The largest net_usd in the population must map to the highest
    percentile (1.0, at-or-below itself and everything smaller)."""
    for i, net in enumerate([-50.0, 10.0, 20.0, -5.0, 100.0]):
        _insert_event(
            conn, position_id=f"p{i}", venue="okx", strategy_id="s1",
            closed_ts=1_700_000_000 + i, net_usd=net,
        )
    samples = compute_gross_edge_proxy(conn, venue="okx", strategy_id="s1")
    by_ts = {s.closed_ts: s.value for s in samples}
    # closed_ts 1_700_000_004 -> net_usd=100.0 (max) -> percentile 1.0
    assert by_ts[1_700_000_004] == pytest.approx(1.0)
    # closed_ts 1_700_000_000 -> net_usd=-50.0 (min) -> percentile 1/5 = 0.2
    assert by_ts[1_700_000_000] == pytest.approx(0.2)


def test_proxy_oldest_first_ordering(conn):
    _insert_event(conn, position_id="p1", venue="okx", strategy_id="s1", closed_ts=300, net_usd=1.0)
    _insert_event(conn, position_id="p2", venue="okx", strategy_id="s1", closed_ts=100, net_usd=2.0)
    _insert_event(conn, position_id="p3", venue="okx", strategy_id="s1", closed_ts=200, net_usd=3.0)
    samples = compute_gross_edge_proxy(conn, venue="okx", strategy_id="s1")
    assert [s.closed_ts for s in samples] == [100, 200, 300]


def test_proxy_scoped_by_venue_and_strategy(conn):
    _insert_event(conn, position_id="p1", venue="okx", strategy_id="s1", closed_ts=100, net_usd=10.0)
    _insert_event(conn, position_id="p2", venue="capital", strategy_id="s1", closed_ts=100, net_usd=999.0)
    _insert_event(conn, position_id="p3", venue="okx", strategy_id="s2", closed_ts=100, net_usd=999.0)
    samples = compute_gross_edge_proxy(conn, venue="okx", strategy_id="s1")
    assert len(samples) == 1
