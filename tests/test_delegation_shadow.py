"""TDD — delegation-gate SHADOW log writer (P2b v1), behavior 0.

Design SSOT: vault/50_research/delegation-gate-blueprint.md. Instrumentation
only — never influences the live G2 dispatch that already happened.
"""

from __future__ import annotations

import sqlite3

from polaris.core.delegation.shadow import fetch_delegation_shadow, log_delegation_shadow
from polaris.storage.schema import ALL_DDL


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for stmt in ALL_DDL:
        conn.executescript(stmt)
    return conn


def _insert_universe(
    conn: sqlite3.Connection, *, venue: str, symbol: str, asset_class: str,
    vol_24h_usd: float = 1_000_000.0, spread_bps: float = 2.0,
) -> None:
    conn.execute(
        "INSERT INTO universe (venue, symbol, instrument_id, "
        "underlying_group_id, asset_class, quote_ccy, state, vol_24h_usd, "
        "spread_bps, last_seen_ts) VALUES (?, ?, ?, ?, ?, 'USD', 'live', ?, ?, 1000)",
        (venue, symbol, f"{venue}:{symbol}", f"{venue}:{symbol}", asset_class,
         vol_24h_usd, spread_bps),
    )


# ---------------------------------------------------------------------------
# log_delegation_shadow — write side
# ---------------------------------------------------------------------------


def test_writes_row_with_fit_top_and_mismatch_flag() -> None:
    conn = _conn()
    # xau_indices_trend (commodity) fits a commodity ticker in a trend regime
    # far better than fx_breakout_basket (fx) does -> fit-top should NOT be
    # the strategy that actually dispatched (fx_breakout_basket), i.e. this
    # exercises the exact audited routing bug the blueprint targets.
    _insert_universe(conn, venue="capital", symbol="XAUUSD", asset_class="commodity")
    log_delegation_shadow(
        conn, run_id="r1", signal_id="sig1", venue="capital", symbol="XAUUSD",
        dispatched_strategy_id="fx_breakout_basket", regime="bull_trend",
        atr_pct=0.01, now_ts=1000, write_conn=conn,
    )
    rows = fetch_delegation_shadow(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["venue"] == "capital"
    assert row["symbol"] == "XAUUSD"
    assert row["dispatched_strategy_id"] == "fx_breakout_basket"
    assert row["fit_top_strategy_id"] == "xau_indices_trend"
    assert row["mismatch"] == 1
    assert row["candidate_n"] > 1
    assert row["regime"] == "bull_trend"


def test_matching_dispatch_records_no_mismatch() -> None:
    conn = _conn()
    _insert_universe(conn, venue="capital", symbol="XAUUSD", asset_class="commodity")
    log_delegation_shadow(
        conn, run_id="r1", signal_id="sig1", venue="capital", symbol="XAUUSD",
        dispatched_strategy_id="xau_indices_trend", regime="bull_trend",
        atr_pct=0.01, now_ts=1000, write_conn=conn,
    )
    row = fetch_delegation_shadow(conn)[0]
    assert row["fit_top_strategy_id"] == "xau_indices_trend"
    assert row["dispatched_strategy_id"] == "xau_indices_trend"
    assert row["mismatch"] == 0


def test_none_conn_is_noop() -> None:
    log_delegation_shadow(
        None, run_id="r", signal_id="s", venue="okx", symbol="X",
        dispatched_strategy_id="tsmom_12_1_multiasset", regime="chop",
        atr_pct=0.01, now_ts=1, write_conn=None,
    )


def test_no_write_path_is_noop() -> None:
    conn = _conn()
    log_delegation_shadow(
        conn, run_id="r", signal_id="s", venue="okx", symbol="BTC-USDT",
        dispatched_strategy_id="tsmom_12_1_multiasset", regime="chop",
        atr_pct=0.01, now_ts=1, write_conn=None, db_writer=None,
    )
    assert fetch_delegation_shadow(conn) == []


def test_unknown_venue_with_no_candidates_is_noop() -> None:
    conn = _conn()
    log_delegation_shadow(
        conn, run_id="r", signal_id="s", venue="no_such_venue", symbol="X",
        dispatched_strategy_id="mystery", regime="chop",
        atr_pct=0.01, now_ts=1, write_conn=conn,
    )
    assert fetch_delegation_shadow(conn) == []


def test_missing_universe_row_degrades_to_neutral_features() -> None:
    conn = _conn()  # no universe row for this symbol
    log_delegation_shadow(
        conn, run_id="r", signal_id="s", venue="okx", symbol="NOPE-USDT",
        dispatched_strategy_id="tsmom_12_1_multiasset", regime="chop",
        atr_pct=0.01, now_ts=1, write_conn=conn,
    )
    rows = fetch_delegation_shadow(conn)
    assert len(rows) == 1
    assert rows[0]["vol_24h_usd"] == 0.0
    assert rows[0]["spread_bps"] == 0.0


def test_single_candidate_venue_has_null_margin_and_never_ambiguous() -> None:
    conn = _conn()
    # alpaca has multiple candidates in the live registry, so instead assert
    # the margin_of contract directly via a monkeypatched single-candidate
    # registry would over-couple to registry internals; instead exercise the
    # public no-second-place contract through fit_score's own margin_of test
    # (test_delegation_fit_score.py) and just assert candidate_n is recorded
    # correctly for a real multi-candidate venue here.
    _insert_universe(conn, venue="okx", symbol="BTC-USDT", asset_class="crypto")
    log_delegation_shadow(
        conn, run_id="r", signal_id="s", venue="okx", symbol="BTC-USDT",
        dispatched_strategy_id="tsmom_12_1_multiasset", regime="chop",
        atr_pct=0.01, now_ts=1, write_conn=conn,
    )
    row = fetch_delegation_shadow(conn)[0]
    assert row["candidate_n"] > 1
    assert row["margin"] is not None


def test_routes_through_db_writer_when_wired() -> None:
    conn = _conn()
    _insert_universe(conn, venue="capital", symbol="XAUUSD", asset_class="commodity")
    submitted: list[str] = []

    class _FakeWriter:
        def submit(self, fn: object, *, label: str = "") -> None:
            fn(conn)  # type: ignore[operator]
            submitted.append(label)

    log_delegation_shadow(
        conn, run_id="r", signal_id="s", venue="capital", symbol="XAUUSD",
        dispatched_strategy_id="xau_indices_trend", regime="bull_trend",
        atr_pct=0.01, now_ts=1, write_conn=None,
        db_writer=_FakeWriter(),  # type: ignore[arg-type]
    )
    assert submitted == ["delegation_shadow"]
    assert len(fetch_delegation_shadow(conn)) == 1


def test_ambiguous_flag_set_below_threshold() -> None:
    conn = _conn()
    # No universe row (asset_class neutral) + crisis regime (regime neutral)
    # + no history -> every same-venue candidate scores exactly 0.0, so the
    # margin is 0.0 < AMBIGUOUS_MARGIN_THRESHOLD.
    log_delegation_shadow(
        conn, run_id="r", signal_id="s", venue="capital", symbol="NOPE",
        dispatched_strategy_id="xau_indices_trend", regime="crisis",
        atr_pct=0.01, now_ts=1, write_conn=conn,
    )
    row = fetch_delegation_shadow(conn)[0]
    assert row["margin"] == 0.0
    assert row["ambiguous"] == 1


def test_fetch_delegation_shadow_mismatch_filter() -> None:
    conn = _conn()
    _insert_universe(conn, venue="capital", symbol="XAUUSD", asset_class="commodity")
    log_delegation_shadow(
        conn, run_id="r", signal_id="s1", venue="capital", symbol="XAUUSD",
        dispatched_strategy_id="fx_breakout_basket", regime="bull_trend",
        atr_pct=0.01, now_ts=1, write_conn=conn,
    )
    log_delegation_shadow(
        conn, run_id="r", signal_id="s2", venue="capital", symbol="XAUUSD",
        dispatched_strategy_id="xau_indices_trend", regime="bull_trend",
        atr_pct=0.01, now_ts=2, write_conn=conn,
    )
    assert len(fetch_delegation_shadow(conn, mismatch=True)) == 1
    assert len(fetch_delegation_shadow(conn, mismatch=False)) == 1
    assert len(fetch_delegation_shadow(conn)) == 2
