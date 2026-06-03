"""Offload TDD — learner tune cooperative yield (loop stays responsive).

The hourly tune is a tight DB+compute blob on the SHARED loop-affine conn
(reads + writes + ``conn.backup``). It is NOT safe to push onto a worker
thread (shared conn). Instead we add an async ``run_once_async`` that runs the
SAME per-learner ``commit_hourly`` work on the loop but yields the loop
(``await asyncio.sleep(0)``) between learners so the tick engine + WS can
interleave during the multi-second cycle.

Pins:
  1. ``run_once_async`` produces the IDENTICAL ``TuneCycleReport`` as the
     synchronous ``run_once`` (golden — same DB mutations, same counts).
  2. The async cycle actually yields to the loop between learners (a
     concurrently-scheduled task gets to run before the cycle completes).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from polaris.core.learners import ClosedTrade, LearnerScheduler
from polaris.storage.schema import init_db


def _trade(*, won: bool, pnl_r: float, ts: int) -> ClosedTrade:
    return ClosedTrade(
        trade_id=f"t{ts}",
        strategy_id="volume_burst",
        ticker="BTC-USDT",
        venue="okx",
        regime="bull_trend",
        session="asia",
        pnl_r=pnl_r,
        won=won,
        holding_bars=20,
        closed_ts=ts,
    )


def _seed(conn: sqlite3.Connection) -> LearnerScheduler:
    sched = LearnerScheduler(conn, expected_holding_bars=20)
    for i in range(25):
        for learner in sched.learners:
            learner.update(_trade(won=True, pnl_r=1.0, ts=i + 1), now_ts=i + 1)
    return sched


def _final_state(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT learner_id, key, value, n_eff, wins_eff, pnl_r_sum_eff, "
        "pending_delta FROM learner_state ORDER BY learner_id, key"
    ).fetchall()


def test_run_once_async_matches_sync_golden(tmp_path: Path) -> None:
    """Async cooperative-yield cycle == sync cycle, row-for-row."""
    sync_db = tmp_path / "sync.sqlite"
    conn_sync = init_db(sync_db)
    async_db = tmp_path / "async.sqlite"
    conn_async = init_db(async_db)
    try:
        sched_sync = _seed(conn_sync)
        sched_async = _seed(conn_async)

        rep_sync = sched_sync.run_once(now_ts=1_000_000)
        rep_async = asyncio.run(sched_async.run_once_async(now_ts=1_000_000))

        assert rep_sync.cycle_ts == rep_async.cycle_ts
        assert sum(r.keys_updated for r in rep_sync.reports) == sum(
            r.keys_updated for r in rep_async.reports
        )
        assert rep_sync.expired_blocks == rep_async.expired_blocks
        assert _final_state(conn_sync) == _final_state(conn_async)
    finally:
        conn_sync.close()
        conn_async.close()


def test_run_once_async_yields_between_learners(tmp_path: Path) -> None:
    """A concurrently-scheduled task must get loop time during the cycle."""
    conn = init_db(tmp_path / "yield.sqlite")
    try:
        sched = _seed(conn)
        marker: list[str] = []

        async def _bystander() -> None:
            # If the tune monopolized the loop with no await points, this would
            # only run AFTER the whole cycle. The per-learner yield lets it
            # interleave (it appends before the cycle's own completion marker).
            await asyncio.sleep(0)
            marker.append("bystander")

        async def _drive() -> None:
            task = asyncio.ensure_future(_bystander())
            await sched.run_once_async(now_ts=2_000_000)
            marker.append("cycle_done")
            await task

        asyncio.run(_drive())
        assert "bystander" in marker
        assert marker.index("bystander") < marker.index("cycle_done")
    finally:
        conn.close()
