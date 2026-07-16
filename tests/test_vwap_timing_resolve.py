"""VWAP timing shadow — offline fill-price resolver (frontgate-scan item #6,
stage 2). DEMO/PAPER virtual capital only.

Mirrors ``gate_kill_counterfactuals``' two-stage precedent: this resolves a
pending ``vwap_timing_shadow`` row's ACTUAL fill price via
``gate_events.position_id`` -> ``positions.side`` ->
``fills.contribution_id = position_id AND is_close = 0``, and computes the
side-aware improvement (bps) vs each known-at-time anchor.

storage-split (round 4 fix): ``vwap_timing_shadow`` is marketdata-domain,
``gate_events``/``positions``/``fills`` are trading-domain — a genuine
cross-domain read. ``resolve_vwap_timing_fills`` takes a marketdata-domain
conn with the trading DB read-only ``ATTACH``ed as ``trading`` (mirrors
``main()``'s production wiring); every fixture here opens TWO separate
sqlite files and attaches them the same way.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from polaris.scripts.vwap_timing_resolve import (
    UNRESOLVABLE_GRACE_SEC,
    resolve_vwap_timing_fills,
)
from polaris.storage.schema import ALL_DDL, init_db
from polaris.storage.schema_marketdata import marketdata_db_path_for

NOW = 1_780_000_000


def _resolver_conn(tmp_path: Path, name: str) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    """(trading_conn, resolver_conn) — resolver_conn is opened on the
    marketdata-domain sibling file with the trading file ATTACHed read-only
    as ``trading`` (matches ``resolve_vwap_timing_fills``'s production wiring
    in ``main()``)."""
    trading_path = tmp_path / f"{name}_trading.sqlite"
    trading_conn = init_db(trading_path)
    md_path = marketdata_db_path_for(trading_path)
    resolver_conn = sqlite3.connect(f"file:{md_path}", uri=True, isolation_level=None)
    for stmt in ALL_DDL:
        resolver_conn.execute(stmt)
    resolver_conn.execute(f"ATTACH DATABASE 'file:{trading_path}?mode=ro' AS trading")
    return trading_conn, resolver_conn


def _seed_shadow_row(
    conn: sqlite3.Connection, *, event_id: str, run_id: str, decision_ts: int,
    session_vwap: float | None = 100.0, avwap: float | None = 100.0,
) -> None:
    conn.execute(
        "INSERT INTO vwap_timing_shadow (event_id, run_id, signal_id, venue, "
        "symbol, decision_ts, current_price, session_vwap, avwap, created_ts) "
        "VALUES (?, ?, 'sig', 'okx', 'BTC-USDT', ?, 100.0, ?, ?, ?)",
        (event_id, run_id, decision_ts, session_vwap, avwap, decision_ts),
    )


def _seed_gate_event(conn: sqlite3.Connection, *, run_id: str, position_id: str) -> None:
    conn.execute(
        "INSERT INTO gate_events (event_id, run_id, gate_id, phase, decision, "
        "position_id, created_ts) VALUES (?, ?, 4, 'entry', 'PROCEED', ?, ?)",
        (uuid.uuid4().hex, run_id, position_id, NOW),
    )


def _seed_position(conn: sqlite3.Connection, *, position_id: str, side: str) -> None:
    conn.execute(
        "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
        "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts) "
        "VALUES (?, 'okx', 'BTC-USDT', 's1', 's1', 's1', ?, 1.0, 'open', ?)",
        (position_id, side, NOW),
    )


def _seed_fill(
    conn: sqlite3.Connection, *, position_id: str, fill_price: float, ts_ms: int,
) -> None:
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        "size_usd, fill_price, ts_ms, order_id, contribution_id, is_close) "
        "VALUES (?, 'okx', 'okx:BTC-USDT', 's1', 'buy', 100.0, ?, ?, 'o1', ?, 0)",
        (uuid.uuid4().hex, fill_price, ts_ms, position_id),
    )


def test_resolve_backfills_long_fill_below_vwap_is_positive_improvement(
    tmp_path: Path,
) -> None:
    trading, resolver = _resolver_conn(tmp_path, "resolve")
    try:
        _seed_shadow_row(resolver, event_id="e1", run_id="r1", decision_ts=NOW - 60,
                          session_vwap=100.0, avwap=100.0)
        _seed_gate_event(trading, run_id="r1", position_id="p1")
        _seed_position(trading, position_id="p1", side="long")
        _seed_fill(trading, position_id="p1", fill_price=99.0, ts_ms=(NOW - 30) * 1000)

        n = resolve_vwap_timing_fills(resolver, now_ts=NOW)
        assert n == 1
        row = resolver.execute(
            "SELECT fill_price, side, session_vwap_improvement_bps, "
            "avwap_improvement_bps, unresolvable FROM vwap_timing_shadow WHERE event_id='e1'"
        ).fetchone()
        assert row[0] == 99.0
        assert row[1] == "long"
        # long, filled BELOW vwap (99 < 100) → positive improvement.
        assert round(row[2], 4) == round((100.0 - 99.0) / 100.0 * 10_000.0, 4)
        assert round(row[3], 4) == round((100.0 - 99.0) / 100.0 * 10_000.0, 4)
        assert row[4] == 0
    finally:
        trading.close()
        resolver.close()


def test_resolve_short_fill_above_vwap_is_positive_improvement(tmp_path: Path) -> None:
    trading, resolver = _resolver_conn(tmp_path, "resolve_short")
    try:
        _seed_shadow_row(resolver, event_id="e2", run_id="r2", decision_ts=NOW - 60,
                          session_vwap=100.0, avwap=None)
        _seed_gate_event(trading, run_id="r2", position_id="p2")
        _seed_position(trading, position_id="p2", side="short")
        _seed_fill(trading, position_id="p2", fill_price=101.0, ts_ms=(NOW - 30) * 1000)

        n = resolve_vwap_timing_fills(resolver, now_ts=NOW)
        assert n == 1
        row = resolver.execute(
            "SELECT session_vwap_improvement_bps, avwap_improvement_bps "
            "FROM vwap_timing_shadow WHERE event_id='e2'"
        ).fetchone()
        # short, filled ABOVE vwap (101 > 100) → positive improvement.
        assert round(row[0], 4) == round((101.0 - 100.0) / 100.0 * 10_000.0, 4)
        assert row[1] is None  # avwap was never resolvable at decision time
    finally:
        trading.close()
        resolver.close()


def test_resolve_no_position_yet_leaves_pending_not_terminal(tmp_path: Path) -> None:
    trading, resolver = _resolver_conn(tmp_path, "pending")
    try:
        _seed_shadow_row(resolver, event_id="e3", run_id="r3", decision_ts=NOW - 60)
        n = resolve_vwap_timing_fills(resolver, now_ts=NOW)
        assert n == 0
        row = resolver.execute(
            "SELECT fill_price, unresolvable, resolve_attempts FROM vwap_timing_shadow WHERE event_id='e3'"
        ).fetchone()
        assert row[0] is None
        assert row[1] == 0
        assert row[2] == 1
    finally:
        trading.close()
        resolver.close()


def test_resolve_marks_unresolvable_after_grace_window(tmp_path: Path) -> None:
    trading, resolver = _resolver_conn(tmp_path, "unresolvable")
    try:
        old_decision_ts = NOW - UNRESOLVABLE_GRACE_SEC - 10
        _seed_shadow_row(resolver, event_id="e4", run_id="r4", decision_ts=old_decision_ts)
        n = resolve_vwap_timing_fills(resolver, now_ts=NOW)
        assert n == 0
        row = resolver.execute(
            "SELECT unresolvable FROM vwap_timing_shadow WHERE event_id='e4'"
        ).fetchone()
        assert row[0] == 1
    finally:
        trading.close()
        resolver.close()


def test_resolve_skips_unresolvable_rows_on_next_pass(tmp_path: Path) -> None:
    trading, resolver = _resolver_conn(tmp_path, "skip")
    try:
        old_decision_ts = NOW - UNRESOLVABLE_GRACE_SEC - 10
        _seed_shadow_row(resolver, event_id="e5", run_id="r5", decision_ts=old_decision_ts)
        resolve_vwap_timing_fills(resolver, now_ts=NOW)
        attempts_before = resolver.execute(
            "SELECT resolve_attempts FROM vwap_timing_shadow WHERE event_id='e5'"
        ).fetchone()[0]
        resolve_vwap_timing_fills(resolver, now_ts=NOW + 3600)
        attempts_after = resolver.execute(
            "SELECT resolve_attempts FROM vwap_timing_shadow WHERE event_id='e5'"
        ).fetchone()[0]
        assert attempts_after == attempts_before  # WHERE unresolvable=0 excludes it now
    finally:
        trading.close()
        resolver.close()


def test_resolve_no_pending_rows_returns_zero(tmp_path: Path) -> None:
    trading, resolver = _resolver_conn(tmp_path, "empty")
    try:
        assert resolve_vwap_timing_fills(resolver, now_ts=NOW) == 0
    finally:
        trading.close()
        resolver.close()
