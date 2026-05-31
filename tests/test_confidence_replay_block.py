"""Confidence replay block TDD — display-only read of the replay read-model.

The replay/benchmark block merges into ``confidence_summary`` (Behaviour 0,
read-only). It reads the ``replay_runs`` + ``benchmark_results`` tables written
offline by the harness; a missing/empty table degrades to ``present=False``.
"""

from __future__ import annotations

import sqlite3

from polaris.core.pipeline.agents.confidence import confidence_summary, replay_block
from polaris.storage.schema import ALL_DDL


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _seed_run(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO replay_runs (run_id, created_ts, bar_interval, n_trades, "
        " net_pnl_real_fee, sharpe, max_dd, win_rate, profit_factor, turnover, "
        " fee_drag_real_r, psr, deflated_sharpe, net_pnl_r_lcb, net_pnl_r_ucb, "
        " is_oos_spread, verdict) "
        "VALUES ('run1', 1000, '1H', 12, 340.5, 1.42, -0.08, 0.58, 1.9, 50000.0, "
        " 6.8, 0.97, 0.94, 0.12, 0.41, 0.05, 'PASS')"
    )
    for tier, base, spread, passed, note in (
        ("relative", "bh_btc", 0.8, 1, "spread>0"),
        ("relative", "tsmom", 0.3, 1, "spread>0"),
        ("risk_adjusted", "", 0.3, 1, "worst spread 0.3"),
        ("statistical", "", 0.0, 1, "PSR=0.97 deflated=0.94 — significant"),
    ):
        conn.execute(
            "INSERT INTO benchmark_results (run_id, tier, baseline, sharpe_spread, "
            " passed, note) VALUES ('run1', ?, ?, ?, ?, ?)",
            (tier, base, spread, passed, note),
        )


def test_replay_block_absent_when_no_runs() -> None:
    conn = _mem()
    blk = replay_block(conn)
    assert blk == {"present": False}


def test_replay_block_reads_latest_run() -> None:
    conn = _mem()
    _seed_run(conn)
    blk = replay_block(conn)
    assert blk["present"] is True
    assert blk["run_id"] == "run1"
    assert blk["net_pnl_real_fee"] == 340.5
    assert blk["psr"] == 0.97
    assert blk["deflated_sharpe"] == 0.94
    assert blk["verdict"] == "PASS"
    assert blk["sharpe_spreads"]["sharpe_spread_vs_bh_btc"] == 0.8
    assert blk["sharpe_spreads"]["sharpe_spread_vs_tsmom"] == 0.3
    # net-R CI band present.
    assert blk["net_pnl_r_lcb"] == 0.12
    assert blk["net_pnl_r_ucb"] == 0.41
    assert blk["is_oos_spread"] == 0.05
    assert any(t["tier"] == "statistical" for t in blk["tiers"])


def test_replay_block_picks_most_recent() -> None:
    conn = _mem()
    _seed_run(conn)
    conn.execute(
        "INSERT INTO replay_runs (run_id, created_ts, net_pnl_real_fee, verdict) "
        "VALUES ('run2', 2000, 999.0, 'EDGE_WIDE_CI')"
    )
    blk = replay_block(conn)
    assert blk["run_id"] == "run2"
    assert blk["net_pnl_real_fee"] == 999.0


def test_confidence_summary_includes_replay_block() -> None:
    conn = _mem()
    _seed_run(conn)
    summary = confidence_summary(conn, starting_equity=10_000.0)
    assert "replay" in summary
    assert summary["replay"]["present"] is True
    assert summary["replay"]["run_id"] == "run1"
