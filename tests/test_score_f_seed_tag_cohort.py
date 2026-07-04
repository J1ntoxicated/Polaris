"""score_F cohort-by-seed_tag helper tests (cowork watchlist-intel Prove-then-Scale).

DEMO/PAPER only. ``score_f_by_seed_tag`` is a READ-ONLY aggregation, joining
the ``score_f_events`` ledger to ``positions.seed_tag`` — it measures whether a
cowork feed's tagged cohort actually earns (Prove-then-Scale principle from
``cowork/watchlist_intel/CONTRACT.md``), it does NOT gate/cap/throttle
anything itself (no consumer wired yet — function + tests only, per task).
Never touches sizing/9-stack/-1.0R (aggressive_always_profit /
no_block_filter_architecture preserved).
"""

from __future__ import annotations

import sqlite3

import pytest

from polaris.core.classes.score_f import rollup_score_f, score_f_by_seed_tag
from tests.test_score_f import _mk_fill, _mk_position


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    from polaris.storage.schema import init_db

    return init_db(tmp_path / "t.sqlite")


def test_cohort_groups_by_seed_tag(conn: sqlite3.Connection) -> None:
    _mk_position(
        conn, position_id="s1", venue="okx", strategy_id="equity_52wk_high_breakout",
        seed_tag="pead_high", closed_ts=1_700_003_600,
    )
    _mk_fill(conn, position_id="s1", venue="okx", symbol="BTC-USDT",
              strategy_id="equity_52wk_high_breakout", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=100.0, is_close=True, ts_ms=1_700_003_000_000)
    _mk_position(
        conn, position_id="s2", venue="okx", strategy_id="equity_52wk_high_breakout",
        seed_tag="sector_rs", closed_ts=1_700_003_700,
    )
    _mk_fill(conn, position_id="s2", venue="okx", symbol="BTC-USDT",
              strategy_id="equity_52wk_high_breakout", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=-50.0, is_close=True, ts_ms=1_700_003_100_000)

    rollup_score_f(conn, now_ts=1_700_010_000)
    cohorts = score_f_by_seed_tag(conn)

    by_tag = {c.seed_tag: c for c in cohorts}
    assert by_tag["pead_high"].n_lifecycles == 1
    assert by_tag["pead_high"].score_f_sum == pytest.approx(100.0)
    assert by_tag["sector_rs"].n_lifecycles == 1
    assert by_tag["sector_rs"].score_f_sum == pytest.approx(-50.0)


def test_cohort_excludes_unseeded_positions(conn: sqlite3.Connection) -> None:
    """seed_tag='' (the overwhelming default, unseeded positions) is never a
    cohort — only actual tags from the intel feed roll up."""
    _mk_position(conn, position_id="u1", seed_tag="", closed_ts=1_700_003_600)
    _mk_fill(conn, position_id="u1", venue="okx", symbol="BTC-USDT",
              strategy_id="gold_riskoff_trend_amplify", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=100.0, is_close=True, ts_ms=1_700_003_000_000)
    rollup_score_f(conn, now_ts=1_700_010_000)

    cohorts = score_f_by_seed_tag(conn)
    assert cohorts == []


def test_cohort_multiple_lifecycles_same_tag_sum_together(conn: sqlite3.Connection) -> None:
    for i, pnl in enumerate([100.0, 50.0, -20.0]):
        pid = f"pead{i}"
        _mk_position(
            conn, position_id=pid, venue="okx",
            strategy_id="equity_52wk_high_breakout", seed_tag="pead_high",
            closed_ts=1_700_003_600 + i,
        )
        _mk_fill(conn, position_id=pid, venue="okx", symbol="BTC-USDT",
                  strategy_id="equity_52wk_high_breakout", size_usd=1000.0,
                  fee_usd=1.0, pnl_usd=pnl, is_close=True,
                  ts_ms=1_700_003_000_000 + i)
    rollup_score_f(conn, now_ts=1_700_010_000)

    cohorts = score_f_by_seed_tag(conn)
    assert len(cohorts) == 1
    assert cohorts[0].seed_tag == "pead_high"
    assert cohorts[0].n_lifecycles == 3
    assert cohorts[0].score_f_sum == pytest.approx(130.0)
    assert cohorts[0].score_f_mean == pytest.approx(130.0 / 3.0)


def test_cohort_empty_ledger_returns_empty_list(conn: sqlite3.Connection) -> None:
    assert score_f_by_seed_tag(conn) == []


def test_cohort_is_read_only_no_writes(conn: sqlite3.Connection) -> None:
    """Calling the cohort helper must not write any row anywhere (pure read)."""
    _mk_position(
        conn, position_id="ro1", venue="okx",
        strategy_id="equity_52wk_high_breakout", seed_tag="insider_buy",
        closed_ts=1_700_003_600,
    )
    _mk_fill(conn, position_id="ro1", venue="okx", symbol="BTC-USDT",
              strategy_id="equity_52wk_high_breakout", size_usd=1000.0,
              fee_usd=1.0, pnl_usd=10.0, is_close=True, ts_ms=1_700_003_000_000)
    rollup_score_f(conn, now_ts=1_700_010_000)
    before = conn.execute("SELECT COUNT(*) FROM score_f_events").fetchone()[0]
    score_f_by_seed_tag(conn)
    score_f_by_seed_tag(conn)
    after = conn.execute("SELECT COUNT(*) FROM score_f_events").fetchone()[0]
    assert before == after
