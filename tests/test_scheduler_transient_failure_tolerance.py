"""TDD — learner scheduler transient-failure tolerance (forensic wf_1f586d0a
tick-body fix #3, learner half).

Before this fix, ANY exception from ``commit_hourly`` (including a transient
one — e.g. a momentary ``database is locked``) permanently set
``learner.enabled = False``, with no path back. Since a disabled learner's
``commit_hourly`` is a silent no-op (never raises), the scheduler never even
observed the learner "recovering" — one bad hour killed that learner's
tuning forever. This tolerates a bounded run of TRANSIENT failures (retry
next cycle, no disable) and only disables on ``LEARNER_PERSISTENT_FAILURE_
THRESHOLD`` CONSECUTIVE failures — flow_not_block, not a defensive throttle:
a single hiccup no longer kills a learner's whole future.

DEMO/PAPER only — Layer 5 learner-network scheduling fix, no sizing/entry
touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polaris.core.learners.base import BaseLearner, ClosedTrade, HourlyCommitReport
from polaris.core.learners.scheduler import (
    LEARNER_PERSISTENT_FAILURE_THRESHOLD,
    LearnerScheduler,
)
from polaris.storage.schema import init_db


class _FlakyLearner(BaseLearner):
    """Minimal BaseLearner whose commit_hourly failure is test-controlled."""

    learner_id = "flaky_test_learner"

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)
        self.fail_next_n = 0
        self.calls = 0

    def key_for(self, trade: ClosedTrade) -> str:
        return "k"

    def observe(self, prior: dict[str, float], trade: ClosedTrade) -> dict[str, float]:
        return prior

    def compute_value_from_stats(self, stats: dict[str, float]) -> float:
        return 1.0

    def commit_hourly(self, *, now_ts: int | None = None) -> HourlyCommitReport:
        self.calls += 1
        if self.fail_next_n > 0:
            self.fail_next_n -= 1
            raise RuntimeError("simulated transient DB fault")
        return HourlyCommitReport(learner_id=self.learner_id, keys_updated=0)


def _sched(conn: sqlite3.Connection) -> tuple[LearnerScheduler, _FlakyLearner]:
    learner = _FlakyLearner(conn)
    sched = LearnerScheduler(conn, learners=[learner])
    return sched, learner


def test_single_transient_failure_does_not_disable(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")
    try:
        sched, learner = _sched(conn)
        learner.fail_next_n = 1
        sched.run_once(now_ts=1_000_000)
        assert learner.enabled is True, (
            "one transient failure must NOT permanently disable the learner"
        )
    finally:
        conn.close()


def test_failure_streak_below_threshold_stays_enabled(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")
    try:
        sched, learner = _sched(conn)
        for i in range(LEARNER_PERSISTENT_FAILURE_THRESHOLD - 1):
            learner.fail_next_n = 1
            sched.run_once(now_ts=1_000_000 + i)
        assert learner.enabled is True
    finally:
        conn.close()


def test_persistent_consecutive_failures_disable(tmp_path: Path) -> None:
    """N CONSECUTIVE failures (the threshold) still disables — persistent
    faults must not retry forever (原 intent preserved)."""
    conn = init_db(tmp_path / "t.sqlite")
    try:
        sched, learner = _sched(conn)
        for i in range(LEARNER_PERSISTENT_FAILURE_THRESHOLD):
            learner.fail_next_n = 1
            sched.run_once(now_ts=1_000_000 + i)
        assert learner.enabled is False
    finally:
        conn.close()


def test_a_success_resets_the_failure_streak(tmp_path: Path) -> None:
    """A successful cycle between failures resets the consecutive-failure
    count — the streak must be CONSECUTIVE, not cumulative."""
    conn = init_db(tmp_path / "t.sqlite")
    try:
        sched, learner = _sched(conn)
        # threshold-1 failures, then a success, then threshold-1 more
        # failures — never THRESHOLD consecutive, so never disabled.
        for i in range(LEARNER_PERSISTENT_FAILURE_THRESHOLD - 1):
            learner.fail_next_n = 1
            sched.run_once(now_ts=2_000_000 + i)
        sched.run_once(now_ts=2_100_000)  # success — resets streak
        for i in range(LEARNER_PERSISTENT_FAILURE_THRESHOLD - 1):
            learner.fail_next_n = 1
            sched.run_once(now_ts=2_200_000 + i)
        assert learner.enabled is True
    finally:
        conn.close()


def test_transient_failure_retries_next_cycle_not_permanently_skipped(
    tmp_path: Path,
) -> None:
    """A learner that failed once must still be CALLED next cycle (retry),
    not silently skipped (that would be indistinguishable from disable)."""
    conn = init_db(tmp_path / "t.sqlite")
    try:
        sched, learner = _sched(conn)
        learner.fail_next_n = 1
        sched.run_once(now_ts=3_000_000)
        assert learner.calls == 1
        sched.run_once(now_ts=3_100_000)
        assert learner.calls == 2, "the scheduler must retry a transiently-failed learner"
    finally:
        conn.close()


async def test_async_path_has_the_same_transient_tolerance(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "t.sqlite")
    try:
        sched, learner = _sched(conn)
        learner.fail_next_n = 1
        await sched.run_once_async(now_ts=4_000_000)
        assert learner.enabled is True
    finally:
        conn.close()
