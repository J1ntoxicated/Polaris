"""Learner/cell dead-strategy prune (#13) — DEMO/PAPER; data-integrity cleanup.

KILLed strategies (e.g. ``volume_burst`` #61, ``fx_range_fade`` wave1) leave
behind learner_state / learner_posterior / cell_matrix rows that are never read
by the live pipeline (it only queries REGISTERED strategy keys) but pollute the
parent2/parent3 roll-ups and the observability dashboard. This prune removes ONLY
rows whose strategy_id is absent from ``STRATEGY_REGISTRY``; live-strategy warming
is preserved (degrade-never-crash, surgical). It dampens nothing and changes NO
trading behaviour — flow_not_block: nothing that flows is removed, and the
immutable ``fills`` audit ledger is never touched.

These tests pin the contract:
  1. PRUNE: dead-strategy rows removed from every learner/cell table; live rows
     untouched (cell_matrix_p0/parent2/parent3/shadow + learner_posterior +
     learner_state across all key formats + learner_blocks).
  2. KEY FORMATS: learner_state strategy token parsed correctly for max_hold /
     regime_mult / session_mult (colon) and triple_stats (pipe).
  3. IDEMPOTENT: a second run deletes 0 rows.
  4. DEGRADE-NEVER-CRASH: empty DB / missing table → no crash, skips gracefully.
  5. EMPTY-LIVE GUARD: refuse to prune against an empty live set (would wipe all).
  6. FILLS UNTOUCHED: the raw fills ledger is never deleted.
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.storage.learner_prune import prune_dead_strategies
from polaris.storage.schema import init_db

_LIVE = {"spot_donchian", "session_breakout"}
_DEAD = "volume_burst"
_DEAD2 = "fx_range_fade"


def _seed_cell_p0(conn: sqlite3.Connection, strategy: str) -> None:
    conn.execute(
        "INSERT INTO cell_matrix_p0 "
        "(exchange, strategy, ticker, regime, n_eff, wins_eff, pnl_r_sum_eff, "
        " avg_pnl_r, score, last_closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("okx", strategy, "BTC-USDT", "chop", 3.0, 1.0, 0.5, 0.16, 0.1, 123),
    )


def _seed_parent2(conn: sqlite3.Connection, strategy: str) -> None:
    conn.execute(
        "INSERT INTO cell_matrix_parent2 "
        "(strategy, regime, n_eff, pnl_r_sum_eff, avg_pnl_r, score, last_closed_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        (strategy, "chop", 3.0, 0.5, 0.16, 0.1, 123),
    )


def _seed_parent3(conn: sqlite3.Connection, strategy: str) -> None:
    conn.execute(
        "INSERT INTO cell_matrix_parent3 "
        "(exchange, strategy, regime, n_eff, pnl_r_sum_eff, avg_pnl_r, score, "
        " last_closed_ts) VALUES (?,?,?,?,?,?,?,?)",
        ("okx", strategy, "chop", 3.0, 0.5, 0.16, 0.1, 123),
    )


def _seed_shadow(conn: sqlite3.Connection, strategy: str) -> None:
    conn.execute(
        "INSERT INTO cell_matrix_shadow_context "
        "(exchange, strategy, ticker, regime, grp, session, n_eff, pnl_r_sum_eff, "
        " avg_pnl_r, score, last_closed_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("okx", strategy, "BTC-USDT", "chop", "g", "s", 3.0, 0.5, 0.16, 0.1, 123),
    )


def _seed_posterior(conn: sqlite3.Connection, strategy: str) -> None:
    conn.execute(
        "INSERT INTO learner_posterior "
        "(exchange, strategy, ticker, regime) VALUES (?,?,?,?)",
        ("okx", strategy, "BTC-USDT", "chop"),
    )


def _seed_regime_prior(conn: sqlite3.Connection, strategy: str) -> None:
    conn.execute(
        "INSERT INTO strategy_regime_prior (strategy, regime) VALUES (?,?)",
        (strategy, "chop"),
    )


def _seed_blocks(conn: sqlite3.Connection, strategy: str) -> None:
    conn.execute(
        "INSERT INTO learner_blocks "
        "(ticker, strategy_id, regime, size_mult, reason, source_learner, "
        " blocked_until_ts, created_ts) VALUES (?,?,?,?,?,?,?,?)",
        ("BTC-USDT", strategy, "chop", 0.3, "r", "triple", 999, 1),
    )


def _seed_state(conn: sqlite3.Connection, learner_id: str, key: str) -> None:
    conn.execute(
        "INSERT INTO learner_state (learner_id, key, value, n_eff, updated_at) "
        "VALUES (?,?,?,?,?)",
        (learner_id, key, 1.0, 2.0, 1),
    )


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _strat_col(conn: sqlite3.Connection, table: str, col: str) -> set[str]:
    return {r[0] for r in conn.execute(f"SELECT {col} FROM {table}")}


def _fresh_db(tmp_path) -> sqlite3.Connection:
    return init_db(tmp_path / "prune.sqlite")


def test_prune_removes_dead_keeps_live(tmp_path) -> None:
    conn = _fresh_db(tmp_path)
    for strat in (*_LIVE, _DEAD, _DEAD2):
        _seed_cell_p0(conn, strat)
        _seed_parent2(conn, strat)
        _seed_parent3(conn, strat)
        _seed_shadow(conn, strat)
        _seed_posterior(conn, strat)
        _seed_regime_prior(conn, strat)
        _seed_blocks(conn, strat)
    # learner_state: every key format, dead + live.
    _seed_state(conn, "max_hold", _DEAD)
    _seed_state(conn, "max_hold", f"{_DEAD}:bucket:1")
    _seed_state(conn, "max_hold", "spot_donchian")
    _seed_state(conn, "regime_mult", f"{_DEAD}:chop")
    _seed_state(conn, "regime_mult", "spot_donchian:chop")
    _seed_state(conn, "session_mult", f"{_DEAD2}:fx_open")
    _seed_state(conn, "session_mult", "session_breakout:fx_open")
    _seed_state(conn, "triple_stats", f"BTC-USDT|{_DEAD}|chop")
    _seed_state(conn, "triple_stats", "BTC-USDT|spot_donchian|chop")
    conn.commit()

    report = prune_dead_strategies(conn, live_strategy_ids=_LIVE)

    # Every cell/posterior/blocks table: only live strategies survive.
    for table, col in (
        ("cell_matrix_p0", "strategy"),
        ("cell_matrix_parent2", "strategy"),
        ("cell_matrix_parent3", "strategy"),
        ("cell_matrix_shadow_context", "strategy"),
        ("learner_posterior", "strategy"),
        ("strategy_regime_prior", "strategy"),
        ("learner_blocks", "strategy_id"),
    ):
        survivors = _strat_col(conn, table, col)
        assert _DEAD not in survivors, table
        assert _DEAD2 not in survivors, table
        assert survivors <= _LIVE, table

    # learner_state: dead keys gone, live keys kept across all key formats.
    surviving = {r[0] for r in conn.execute("SELECT key FROM learner_state")}
    assert all(_DEAD not in k and _DEAD2 not in k for k in surviving)
    assert "spot_donchian" in surviving
    assert "spot_donchian:chop" in surviving
    assert "session_breakout:fx_open" in surviving
    assert "BTC-USDT|spot_donchian|chop" in surviving

    assert report.total_deleted > 0
    assert report.by_table["cell_matrix_p0"] == 2  # 2 dead strategies
    # learner_state dead keys: 2 max_hold + 1 regime + 1 session + 1 triple = 5
    assert report.by_table["learner_state"] == 5


def test_idempotent_second_run_deletes_zero(tmp_path) -> None:
    conn = _fresh_db(tmp_path)
    _seed_cell_p0(conn, _DEAD)
    _seed_cell_p0(conn, "spot_donchian")
    _seed_state(conn, "regime_mult", f"{_DEAD}:chop")
    conn.commit()

    first = prune_dead_strategies(conn, live_strategy_ids=_LIVE)
    assert first.total_deleted > 0

    second = prune_dead_strategies(conn, live_strategy_ids=_LIVE)
    assert second.total_deleted == 0
    assert _count(conn, "cell_matrix_p0") == 1  # live row preserved


def test_empty_db_no_crash(tmp_path) -> None:
    conn = _fresh_db(tmp_path)
    report = prune_dead_strategies(conn, live_strategy_ids=_LIVE)
    assert report.total_deleted == 0


def test_missing_table_degrades(tmp_path) -> None:
    conn = _fresh_db(tmp_path)
    conn.execute("DROP TABLE learner_posterior")
    _seed_cell_p0(conn, _DEAD)
    conn.commit()
    # Must not raise even though one expected table is gone.
    report = prune_dead_strategies(conn, live_strategy_ids=_LIVE)
    assert report.by_table["cell_matrix_p0"] == 1


def test_empty_live_set_refuses(tmp_path) -> None:
    conn = _fresh_db(tmp_path)
    _seed_cell_p0(conn, _DEAD)
    conn.commit()
    with pytest.raises(ValueError):
        prune_dead_strategies(conn, live_strategy_ids=set())
    # Nothing deleted on refusal.
    assert _count(conn, "cell_matrix_p0") == 1


def test_fills_ledger_untouched(tmp_path) -> None:
    conn = _fresh_db(tmp_path)
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, ts_ms, order_id) VALUES (?,?,?,?,?,?,?,?,?)",
        ("f1", "okx", "BTC-USDT", _DEAD, "buy", 100.0, 1.0, 1, "o1"),
    )
    _seed_cell_p0(conn, _DEAD)
    conn.commit()
    prune_dead_strategies(conn, live_strategy_ids=_LIVE)
    assert _count(conn, "fills") == 1  # immutable audit ledger preserved
    assert _count(conn, "cell_matrix_p0") == 0  # derived row pruned
