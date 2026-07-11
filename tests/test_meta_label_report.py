"""Meta-label accumulation report + purged-split prep (frontgate-scan item
#10) — TDD.

DEMO/PAPER · behavior-0 · READ-ONLY over the existing meta_labels table
(collection unchanged — meta_label.py stays the only writer).
"""

from __future__ import annotations

import sqlite3
import uuid

from polaris.core.learners.meta_label import (
    compute_triple_barrier_label,
    persist_meta_label,
)
from polaris.core.learners.meta_label_report import (
    PER_STRATEGY_LABEL_GATE,
    POOLED_LABEL_GATE,
    build_distribution_report,
    load_timed_observations,
)
from polaris.storage.schema import init_db

NOW = 1_780_000_000


def _conn() -> sqlite3.Connection:
    return init_db(":memory:")


def _seed_label(
    conn: sqlite3.Connection, *, strategy_id: str, barrier_pnl_r: float, won: bool,
    holding_bars: int = 8, regime: str = "trend_up", session: str = "asia",
    created_ts: int = NOW,
) -> None:
    label = compute_triple_barrier_label(
        pnl_r=barrier_pnl_r, won=won, holding_bars=holding_bars,
        expected_holding_bars=20,
    )
    persist_meta_label(
        conn, trade_id=uuid.uuid4().hex, strategy_id=strategy_id, venue="okx",
        ticker="BTC-USDT", regime=regime, session=session, label=label,
        now_ts=created_ts,
    )


# ---------------------------------------------------------------------------
# build_distribution_report
# ---------------------------------------------------------------------------


def test_empty_table_reports_zero_and_gate_unmet() -> None:
    conn = _conn()
    report = build_distribution_report(conn)
    assert report.total == 0
    assert report.pooled_gate_met is False
    assert report.strategies_gate_met == {}


def test_distribution_counts_per_dimension() -> None:
    conn = _conn()
    _seed_label(conn, strategy_id="tsmom", barrier_pnl_r=2.0, won=True)
    _seed_label(conn, strategy_id="tsmom", barrier_pnl_r=-1.5, won=False)
    _seed_label(conn, strategy_id="rsi_bb", barrier_pnl_r=0.3, won=True)
    report = build_distribution_report(conn)
    assert report.total == 3
    assert report.per_strategy == {"tsmom": 2, "rsi_bb": 1}
    assert report.per_barrier == {"tp": 1, "sl": 1, "timeout": 1}
    assert report.per_regime == {"trend_up": 3}
    # SESSION CAVEAT (documented in the module docstring): every seeded row
    # used the same hardcoded "asia" string — reflects the writer's known
    # constant, not a real per-trade signal.
    assert report.per_session == {"asia": 3}


def test_pooled_gate_threshold_boundary() -> None:
    conn = _conn()
    for i in range(POOLED_LABEL_GATE - 1):
        _seed_label(conn, strategy_id=f"s{i % 5}", barrier_pnl_r=0.1, won=True)
    report = build_distribution_report(conn)
    assert report.total == POOLED_LABEL_GATE - 1
    assert report.pooled_gate_met is False

    _seed_label(conn, strategy_id="s0", barrier_pnl_r=0.1, won=True)
    report2 = build_distribution_report(conn)
    assert report2.total == POOLED_LABEL_GATE
    assert report2.pooled_gate_met is True


def test_per_strategy_gate_flags_each_strategy_independently() -> None:
    conn = _conn()
    for _ in range(PER_STRATEGY_LABEL_GATE):
        _seed_label(conn, strategy_id="tsmom", barrier_pnl_r=0.1, won=True)
    for _ in range(PER_STRATEGY_LABEL_GATE - 1):
        _seed_label(conn, strategy_id="rsi_bb", barrier_pnl_r=0.1, won=True)
    report = build_distribution_report(conn)
    assert report.strategies_gate_met["tsmom"] is True
    assert report.strategies_gate_met["rsi_bb"] is False


# ---------------------------------------------------------------------------
# load_timed_observations
# ---------------------------------------------------------------------------


def test_timed_observations_infer_start_ts_from_holding_bars() -> None:
    conn = _conn()
    _seed_label(
        conn, strategy_id="tsmom", barrier_pnl_r=1.0, won=True,
        holding_bars=10, created_ts=NOW,
    )
    obs = load_timed_observations(conn)
    assert len(obs) == 1
    # holding_bars=10 -> 10 * 60s = 600s before created_ts (the 1-minute-bar
    # conversion _safe_record_meta_label uses).
    assert obs[0].end_ts == NOW
    assert obs[0].start_ts == NOW - 600


def test_timed_observations_count_matches_label_count() -> None:
    conn = _conn()
    for i in range(5):
        _seed_label(
            conn, strategy_id="tsmom", barrier_pnl_r=0.1, won=True,
            created_ts=NOW + i * 60,
        )
    obs = load_timed_observations(conn)
    assert len(obs) == 5


def test_timed_observations_feed_purged_time_splits() -> None:
    """Integration smoke: the loader's output is directly consumable by
    purged_time_splits (item #10's split utility) with no adaptation."""
    from polaris.core.benchmark.purged_time_split import purged_time_splits

    conn = _conn()
    for i in range(20):
        _seed_label(
            conn, strategy_id="tsmom", barrier_pnl_r=0.1, won=True,
            holding_bars=2, created_ts=NOW + i * 3600,
        )
    obs = load_timed_observations(conn)
    splits = purged_time_splits(obs, n_splits=1, embargo_sec=60)
    assert len(splits) == 1
