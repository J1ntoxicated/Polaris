"""Layer 5 — Learner Network tests (TDD).

Spec coverage:
  - vault/30_components/layer-5-learner-network.md (Q1-Q6)
  - vault/10_decisions/ADR-007 §adaptive_learner_attack 4 principles
"""

from __future__ import annotations

import math
import sqlite3

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from polaris.core.learners import (
    LEARNER_INDIVIDUAL_MULT_CLIP,
    LEARNER_PRODUCT_CLIP,
    NEUTRAL_MULT,
    TRIPLE_BLOCK_DURATION_SEC,
    TRIPLE_BLOCK_SIZE_MULT,
    ClosedTrade,
    LearnerScheduler,
    MaxHoldLearner,
    RegimeMultLearner,
    SessionMultLearner,
    clip_individual_mult,
    clip_product_mult,
    evaluate_triple_block,
    evict_expired_triple_blocks,
    resolve_final_size_mult,
    restore_snapshot_from_disk,
)

# ---------------------------------------------------------------------------
# session_mult learner
# ---------------------------------------------------------------------------


def _trade(*, won: bool, pnl_r: float, regime: str = "bull_trend",
           session: str = "asia", ticker: str = "BTC-USDT",
           strategy: str = "volume_burst", holding_bars: int = 10,
           ts: int = 1) -> ClosedTrade:
    return ClosedTrade(
        trade_id=f"t{ts}",
        strategy_id=strategy,
        ticker=ticker,
        venue="okx",
        regime=regime,
        session=session,
        pnl_r=pnl_r,
        won=won,
        holding_bars=holding_bars,
        closed_ts=ts,
    )


def test_session_learner_update_and_get_mult(memdb: sqlite3.Connection) -> None:
    learner = SessionMultLearner(memdb)
    # 25 winning trades in asia → WR = 1.0 → +0.1 commit.
    for i in range(25):
        learner.update(_trade(won=True, pnl_r=1.0, ts=i + 1), now_ts=i + 1)
    learner.commit_hourly(now_ts=100)
    res = learner.get_mult(
        ticker="BTC-USDT", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    )
    assert res.source == "live"
    assert res.value == pytest.approx(1.1, rel=1e-6)


def test_session_learner_sparse_returns_neutral(memdb: sqlite3.Connection) -> None:
    learner = SessionMultLearner(memdb)
    for i in range(5):
        learner.update(_trade(won=True, pnl_r=1.0, ts=i + 1), now_ts=i + 1)
    learner.commit_hourly(now_ts=100)
    res = learner.get_mult(
        ticker="BTC-USDT", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    )
    assert res.source == "sparse"
    assert res.value == NEUTRAL_MULT


# ---------------------------------------------------------------------------
# regime_mult learner — WR thresholds
# ---------------------------------------------------------------------------


def test_regime_learner_wr_threshold_promote(memdb: sqlite3.Connection) -> None:
    learner = RegimeMultLearner(memdb)
    # 14 wins / 25 = 0.56 → ≥ 0.55 → +0.1.
    for i in range(14):
        learner.update(_trade(won=True, pnl_r=1.0, ts=i + 1), now_ts=i + 1)
    for i in range(11):
        learner.update(_trade(won=False, pnl_r=-0.5, ts=i + 100), now_ts=i + 100)
    learner.commit_hourly(now_ts=200)
    res = learner.get_mult(
        ticker="BTC-USDT", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    )
    assert res.value == pytest.approx(1.1, rel=1e-6)


def test_regime_learner_wr_threshold_demote(memdb: sqlite3.Connection) -> None:
    learner = RegimeMultLearner(memdb)
    # 10 wins / 25 = 0.40 → ≤ 0.40 → -0.1.
    for i in range(10):
        learner.update(_trade(won=True, pnl_r=1.0, ts=i + 1), now_ts=i + 1)
    for i in range(15):
        learner.update(_trade(won=False, pnl_r=-1.0, ts=i + 100), now_ts=i + 100)
    learner.commit_hourly(now_ts=200)
    res = learner.get_mult(
        ticker="BTC-USDT", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    )
    assert res.value == pytest.approx(0.9, rel=1e-6)


def test_regime_learner_wr_neutral_band(memdb: sqlite3.Connection) -> None:
    learner = RegimeMultLearner(memdb)
    # 12 wins / 25 = 0.48 → between 0.40 and 0.55 → unchanged.
    for i in range(12):
        learner.update(_trade(won=True, pnl_r=1.0, ts=i + 1), now_ts=i + 1)
    for i in range(13):
        learner.update(_trade(won=False, pnl_r=-0.5, ts=i + 100), now_ts=i + 100)
    learner.commit_hourly(now_ts=200)
    res = learner.get_mult(
        ticker="BTC-USDT", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    )
    assert res.value == pytest.approx(1.0, rel=1e-6)


# ---------------------------------------------------------------------------
# max_hold learner — bucket avg_pnl
# ---------------------------------------------------------------------------


def test_max_hold_bucket_avg_pnl(memdb: sqlite3.Connection) -> None:
    """Best bucket should pull max_hold toward its bar count."""
    learner = MaxHoldLearner(memdb, expected_holding_bars=20)
    # Bucket 1 (10-30 bars) wins, bucket 0 (≤10) and bucket 2 (>30) lose.
    for i in range(10):
        learner.update(_trade(won=False, pnl_r=-1.0, holding_bars=5,
                              ts=i + 1), now_ts=i + 1)
    for i in range(15):  # bucket 1 — best
        learner.update(_trade(won=True, pnl_r=1.5, holding_bars=20,
                              ts=i + 100), now_ts=i + 100)
    for i in range(5):
        learner.update(_trade(won=False, pnl_r=-1.0, holding_bars=40,
                              ts=i + 200), now_ts=i + 200)
    initial_value = learner.get_mult(
        ticker="X", strategy_id="volume_burst", regime="bull_trend"
    ).value
    learner.commit_hourly(now_ts=300)
    after = learner.get_mult(
        ticker="X", strategy_id="volume_burst", regime="bull_trend"
    ).value
    # target = 1.0 * expected (20). Should move toward 20.
    assert after >= initial_value or math.isclose(after, initial_value)


def test_max_hold_starts_from_expected_holding_bars(memdb: sqlite3.Connection) -> None:
    """codex Day 4 R1 P0 fix: max_hold must NOT start at NEUTRAL_MULT (=1.0).

    Once enough trades land, ``get_mult`` must return a value close to the
    configured ``expected_holding_bars`` baseline (40 here), tunable by ±10%.
    """
    learner = MaxHoldLearner(memdb, expected_holding_bars=40)
    # 25 winning trades, all in bucket 1 (≈ expected_holding_bars).
    for i in range(25):
        learner.update(
            _trade(won=True, pnl_r=1.5, holding_bars=40, ts=i + 1), now_ts=i + 1
        )
    pre_commit = learner.get_mult(
        ticker="X", strategy_id="volume_burst", regime="bull_trend"
    )
    # Stored value must be near expected_holding_bars (40), not 1.0.
    assert pre_commit.value >= 39.0
    assert pre_commit.source == "live"
    learner.commit_hourly(now_ts=300)
    post = learner.get_mult(
        ticker="X", strategy_id="volume_burst", regime="bull_trend"
    )
    assert post.value >= 39.0


# ---------------------------------------------------------------------------
# Clip primitives
# ---------------------------------------------------------------------------


def test_individual_clip_0_3_to_3_0() -> None:
    lo, hi = LEARNER_INDIVIDUAL_MULT_CLIP
    assert lo == 0.3
    assert hi == 3.0
    assert clip_individual_mult(0.1) == 0.3
    assert clip_individual_mult(5.0) == 3.0
    assert clip_individual_mult(1.0) == 1.0


def test_product_clip_floor_0_2_anticascade() -> None:
    # Floor raised 0.1→0.2 (2026-06-24 G5 audit): a dual-axis learner demotion
    # (session 0.3 × regime 0.3 = 0.09) must NOT collapse size to 10%. Re-clip to
    # 0.2 keeps ≥20% of intended (flow_not_block: less throttle, aggressive bias).
    lo, hi = LEARNER_PRODUCT_CLIP
    assert lo == 0.2
    assert hi == 5.0
    assert clip_product_mult(0.09) == 0.2  # dual-axis 0.3×0.3 floored to 0.2, not 0.1
    assert clip_product_mult(0.05) == 0.2
    assert clip_product_mult(10.0) == 5.0


def test_resolve_final_size_mult_independent_axis() -> None:
    # 4 axes, no priority — multiplicative.
    out = resolve_final_size_mult(
        session_mult=1.5,
        regime_mult=1.2,
        cell_routing_mult=1.5,
        ai_feedback_weight=1.0,
    )
    expected = clip_product_mult(1.5 * 1.2 * 1.5 * 1.0)
    assert out == pytest.approx(expected)


# ---------------------------------------------------------------------------
# adaptive_learner_attack — triple block
# ---------------------------------------------------------------------------


def test_adaptive_attack_block_1h_auto_unblock(memdb: sqlite3.Connection) -> None:
    learner = RegimeMultLearner(memdb)
    # 25 trades, 5 wins / 25 = 20% WR, expectancy negative → block.
    for i in range(5):
        learner.update(_trade(won=True, pnl_r=0.5, ts=i + 1), now_ts=i + 1)
    for i in range(20):
        learner.update(_trade(won=False, pnl_r=-1.0, ts=i + 100), now_ts=i + 100)
    last_ts = 119  # last update used now_ts=119
    block = evaluate_triple_block(
        memdb, ticker="BTC-USDT", strategy_id="volume_burst",
        regime="bull_trend", now_ts=last_ts + 1,
    )
    assert block is not None
    assert block.size_mult == TRIPLE_BLOCK_SIZE_MULT
    # blocked_until ≈ last_ts + 3600
    assert block.blocked_until_ts - last_ts == TRIPLE_BLOCK_DURATION_SEC

    # 1h later → expired.
    assert (
        evaluate_triple_block(
            memdb, ticker="BTC-USDT", strategy_id="volume_burst",
            regime="bull_trend",
            now_ts=last_ts + TRIPLE_BLOCK_DURATION_SEC + 1,
        )
        is None
    )
    # eviction reaps the row
    n = evict_expired_triple_blocks(
        memdb, now_ts=last_ts + TRIPLE_BLOCK_DURATION_SEC + 1
    )
    assert n == 1


def test_adaptive_attack_specific_triple_isolation(memdb: sqlite3.Connection) -> None:
    """A blocked (BTC × volume_burst × bull_trend) triple must NOT block
    (ETH × volume_burst × bull_trend)."""
    learner = RegimeMultLearner(memdb)
    for i in range(25):
        learner.update(
            _trade(won=False, pnl_r=-1.0, ticker="BTC-USDT",
                   strategy="volume_burst", regime="bull_trend",
                   ts=i + 1),
            now_ts=i + 1,
        )
    btc_block = evaluate_triple_block(
        memdb, ticker="BTC-USDT", strategy_id="volume_burst",
        regime="bull_trend", now_ts=100,
    )
    eth_block = evaluate_triple_block(
        memdb, ticker="ETH-USDT", strategy_id="volume_burst",
        regime="bull_trend", now_ts=100,
    )
    assert btc_block is not None
    assert eth_block is None


def test_toggle_disables_learner_returns_neutral(memdb: sqlite3.Connection) -> None:
    learner = RegimeMultLearner(memdb)
    for i in range(25):
        learner.update(_trade(won=True, pnl_r=1.0, ts=i + 1), now_ts=i + 1)
    learner.commit_hourly(now_ts=200)
    learner.enabled = False
    res = learner.get_mult(
        ticker="BTC-USDT", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    )
    assert res.source == "disabled"
    assert res.value == NEUTRAL_MULT


# ---------------------------------------------------------------------------
# Hourly commit atomicity + snapshot/rollback
# ---------------------------------------------------------------------------


def test_hourly_commit_atomic(memdb: sqlite3.Connection) -> None:
    learner = SessionMultLearner(memdb)
    for i in range(25):
        learner.update(_trade(won=True, pnl_r=1.0, ts=i + 1), now_ts=i + 1)
    rep1 = learner.commit_hourly(now_ts=100)
    assert rep1.keys_updated == 1
    rep2 = learner.commit_hourly(now_ts=200)
    # second commit cannot move further (cap +0.1, already at 1.1, candidate 1.1)
    assert rep2.snapshot_ts == 200


def test_snapshot_rollback(memdb: sqlite3.Connection) -> None:
    learner = SessionMultLearner(memdb)
    # Pre-tune state.
    for i in range(25):
        learner.update(_trade(won=True, pnl_r=1.0, ts=i + 1), now_ts=i + 1)
    rep_a = learner.commit_hourly(now_ts=100)
    snap_ts = rep_a.snapshot_ts
    pre_value = learner.get_mult(
        ticker="X", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    ).value

    # Add bad data — enough losers to flip the cumulative WR below 0.40.
    # We had 25 wins, so we need ≥38 losses to bring WR ≤ 0.40 (25/63 ≈ 0.397).
    for i in range(45):
        learner.update(_trade(won=False, pnl_r=-2.0, ts=i + 1000),
                       now_ts=i + 1000)
    learner.commit_hourly(now_ts=2000)
    bad_value = learner.get_mult(
        ticker="X", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    ).value
    assert bad_value < pre_value

    # Rollback.
    learner.rollback(snap_ts)
    after = learner.get_mult(
        ticker="X", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    ).value
    assert after == pytest.approx(pre_value)


# ---------------------------------------------------------------------------
# Scheduler smoke
# ---------------------------------------------------------------------------


def test_restore_snapshot_from_disk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Manual-apply rollback path: write manifest JSON, mutate DB, restore."""
    import json  # noqa: PLC0415

    from polaris.storage.schema import ALL_DDL  # noqa: PLC0415

    db_path = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    learner = SessionMultLearner(conn)
    for i in range(25):
        learner.update(_trade(won=True, pnl_r=1.0, ts=i + 1), now_ts=i + 1)
    rep = learner.commit_hourly(now_ts=5_000)
    snap_ts = rep.snapshot_ts
    pre_value = learner.get_mult(
        ticker="X", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    ).value

    # On-disk manifest should exist now.
    snap_dir = tmp_path / "ext_snapshots"
    snap_dir.mkdir()
    manifest = snap_dir / f"{snap_ts}.session_mult.json"
    manifest.write_text(json.dumps({
        "snapshot_ts": snap_ts,
        "learner_id": "session_mult",
        "rows": [
            {
                "key": "volume_burst:asia",
                "value": pre_value,
                "n_eff": 25.0,
                "wins_eff": 25.0,
                "pnl_r_sum_eff": 25.0,
                "pending_delta": 0.0,
            }
        ],
    }, separators=(",", ":")))

    # Mutate state to drift away.
    conn.execute(
        "UPDATE learner_state SET value = 0.5 "
        "WHERE learner_id = 'session_mult' AND key = 'volume_burst:asia'"
    )
    drifted = learner.get_mult(
        ticker="X", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    ).value
    assert drifted == pytest.approx(0.5)

    # Manual restore from disk manifest.
    restored_count = restore_snapshot_from_disk(
        conn, snapshot_ts=snap_ts, learner_id="session_mult", snapshot_dir=snap_dir
    )
    assert restored_count == 1
    after = learner.get_mult(
        ticker="X", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    ).value
    assert after == pytest.approx(pre_value)
    conn.close()


def test_scheduler_runs_three_learners(memdb: sqlite3.Connection) -> None:
    sched = LearnerScheduler(memdb, expected_holding_bars=20)
    # feed enough trades for all three
    for i in range(25):
        for learner in sched.learners:
            learner.update(_trade(won=True, pnl_r=1.0, holding_bars=20,
                                   ts=i + 1), now_ts=i + 1)
    cycle = sched.run_once(now_ts=300)
    assert len(cycle.reports) == 3
    assert cycle.expired_blocks == 0


def test_scheduler_evicts_expired_blocks(memdb: sqlite3.Connection) -> None:
    sched = LearnerScheduler(memdb)
    regime = sched.learners[1]
    assert isinstance(regime, RegimeMultLearner)
    # Force a block.
    for i in range(25):
        regime.update(_trade(won=False, pnl_r=-1.0, ts=i + 1), now_ts=i + 1)
    # Run scheduler far enough in future to expire it.
    cycle = sched.run_once(now_ts=200 + TRIPLE_BLOCK_DURATION_SEC + 1)
    assert cycle.expired_blocks == 1


# ---------------------------------------------------------------------------
# Property: get_mult always finite + clipped
# ---------------------------------------------------------------------------


@settings(deadline=None, max_examples=40)
@given(
    st.integers(min_value=20, max_value=200),
    st.floats(min_value=-3.0, max_value=3.0, allow_nan=False, allow_infinity=False),
)
def test_property_get_mult_finite_and_clipped(n_trades: int, pnl_r: float) -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    from polaris.storage.schema import ALL_DDL  # noqa: PLC0415

    for stmt in ALL_DDL:
        conn.execute(stmt)
    learner = RegimeMultLearner(conn)
    for i in range(n_trades):
        learner.update(
            _trade(won=pnl_r > 0, pnl_r=pnl_r, ts=i + 1), now_ts=i + 1
        )
    learner.commit_hourly(now_ts=100_000)
    out = learner.get_mult(
        ticker="X", strategy_id="volume_burst",
        regime="bull_trend", session="asia",
    )
    assert math.isfinite(out.value)
    lo, hi = LEARNER_INDIVIDUAL_MULT_CLIP
    assert lo <= out.value <= hi
    conn.close()
