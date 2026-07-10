"""Layer 5 — hourly auto-tune scheduler (asyncio cron-style).

Spec source:
- vault/30_components/layer-5-learner-network.md (Q1, Q3, Q4)
- ADR-007 §Hourly Tune Job

Coordinates the 3 P0 learners (session_mult / regime_mult / max_hold). Each
hour:
  1. ``commit_hourly()`` per learner — atomic delta write + snapshot.
  2. ``evict_expired_triple_blocks()`` — 1h auto-unblock sweep.
  3. (optional) rollback candidate detection — placeholder, manual-apply only
     per ADR-007 (no auto-apply).

P0 = single-shot ``run_once()`` (driven by asyncio loop). P1 will replace this
with a long-lived ``run_forever()`` co-routine.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from polaris.core.learners.base import (
    BaseLearner,
    HourlyCommitReport,
    evict_expired_triple_blocks,
)
from polaris.core.learners.max_hold import MaxHoldLearner
from polaris.core.learners.regime import RegimeMultLearner
from polaris.core.learners.session import SessionMultLearner

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SEC = 3600

# Forensic wf_1f586d0a tick-body fix #3 (Jin 2026-07-10): a learner used to be
# permanently disabled on the FIRST ``commit_hourly`` exception — including a
# transient one (e.g. a momentary ``database is locked``). A disabled
# learner's ``commit_hourly`` is a silent no-op (never raises — see
# ``BaseLearner.commit_hourly``), so the scheduler could never observe
# "recovery" once tripped: one bad hour killed that learner's tuning forever.
# The threshold below tolerates a bounded run of CONSECUTIVE transient
# failures (retried next cycle, no disable) and only disables on a genuinely
# PERSISTENT fault streak — mirrors the strategy circuit breaker's
# ``CB_EXCEPTION_THRESHOLD`` count (3) in ``polaris/core/isolation/
# circuit_breaker.py``. flow_not_block: this is fault-tolerance/observability,
# not a throttle — a healthy learner's cadence/behaviour is unchanged.
LEARNER_PERSISTENT_FAILURE_THRESHOLD = 3


@dataclass(slots=True)
class TuneCycleReport:
    cycle_ts: int
    reports: list[HourlyCommitReport] = field(default_factory=list)
    expired_blocks: int = 0


class LearnerScheduler:
    """Owns the trio of P0 learners + hourly trigger.

    Construct once per process; call ``run_once`` from a periodic asyncio
    coroutine or directly from the harness loop.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        expected_holding_bars: int = 60,
        learners: Iterable[BaseLearner] | None = None,
    ) -> None:
        self.conn = conn
        if learners is None:
            self.learners = [
                SessionMultLearner(conn),
                RegimeMultLearner(conn),
                MaxHoldLearner(conn, expected_holding_bars=expected_holding_bars),
            ]
        else:
            self.learners = list(learners)
        # Per-learner CONSECUTIVE failure streak (fix #3) — reset to 0 on any
        # successful commit_hourly, only crosses the disable threshold on a
        # genuinely persistent run of failures.
        self._consecutive_failures: dict[str, int] = {}

    def _commit_one(self, learner: BaseLearner, *, ts: int) -> HourlyCommitReport | None:
        """Run one learner's ``commit_hourly``, applying the transient-failure
        tolerance. Returns the report on success, ``None`` on failure (the
        caller skips appending it — same shape as before this fix)."""
        try:
            report = learner.commit_hourly(now_ts=ts)
        except Exception as exc:  # noqa: BLE001 — capture for streak/toggle
            streak = self._consecutive_failures.get(learner.learner_id, 0) + 1
            self._consecutive_failures[learner.learner_id] = streak
            if streak >= LEARNER_PERSISTENT_FAILURE_THRESHOLD:
                logger.error(
                    "[scheduler] learner %s commit_hourly failed %d consecutive "
                    "times (>= threshold %d) — disabling: %r",
                    learner.learner_id, streak,
                    LEARNER_PERSISTENT_FAILURE_THRESHOLD, exc,
                )
                learner.enabled = False
            else:
                logger.warning(
                    "[scheduler] learner %s commit_hourly failed (%d/%d "
                    "transient) — retrying next cycle: %r",
                    learner.learner_id, streak,
                    LEARNER_PERSISTENT_FAILURE_THRESHOLD, exc,
                )
            return None
        self._consecutive_failures[learner.learner_id] = 0
        return report

    def run_once(self, *, now_ts: int | None = None) -> TuneCycleReport:
        ts = int(now_ts if now_ts is not None else time.time())
        cycle = TuneCycleReport(cycle_ts=ts)
        logger.info(
            "[scheduler] tune cycle start ts=%d learners=%d",
            ts,
            len(self.learners),
        )
        for learner in self.learners:
            report = self._commit_one(learner, ts=ts)
            if report is not None:
                cycle.reports.append(report)
        cycle.expired_blocks = evict_expired_triple_blocks(self.conn, now_ts=ts)
        logger.info(
            "[scheduler] tune cycle end keys_updated_total=%d expired_blocks=%d",
            sum(r.keys_updated for r in cycle.reports),
            cycle.expired_blocks,
        )
        return cycle

    async def run_once_async(self, *, now_ts: int | None = None) -> TuneCycleReport:
        """Cooperative-yield twin of ``run_once`` (keeps the loop responsive).

        The tune is a tight DB+compute blob on the SHARED loop-affine conn
        (read + ``compute_value_from_stats`` + write + ``conn.backup`` inside
        ``commit_hourly``). It is NOT safe to push onto a worker thread — the
        shared conn must stay on the loop. The pure ``nig`` / value math is
        negligible (96 rows), so threading it would buy nothing. Instead we run
        the IDENTICAL work on the loop but ``await asyncio.sleep(0)`` between
        learners so the tick engine + WS interleave during the multi-second
        cycle (which is dominated by the per-learner full-DB disk backup).

        Same DB mutations + same ``TuneCycleReport`` as ``run_once``.
        """
        ts = int(now_ts if now_ts is not None else time.time())
        cycle = TuneCycleReport(cycle_ts=ts)
        logger.info(
            "[scheduler] tune cycle start (async) ts=%d learners=%d",
            ts,
            len(self.learners),
        )
        for learner in self.learners:
            report = self._commit_one(learner, ts=ts)
            if report is not None:
                cycle.reports.append(report)
            # Cooperative yield: let the tick engine + WS run between learners
            # so a slow per-learner backup doesn't monopolize the loop. Safe —
            # still on the loop thread, no shared-conn race.
            await asyncio.sleep(0)
        cycle.expired_blocks = evict_expired_triple_blocks(self.conn, now_ts=ts)
        logger.info(
            "[scheduler] tune cycle end (async) keys_updated_total=%d expired_blocks=%d",
            sum(r.keys_updated for r in cycle.reports),
            cycle.expired_blocks,
        )
        return cycle

    async def run_forever(self, *, interval_sec: int = DEFAULT_INTERVAL_SEC) -> None:
        """Long-lived asyncio coroutine — sleeps then triggers cycle.

        ``interval_sec=3600`` per ADR-007 (hourly). Tests can override.
        """
        logger.info(
            "[scheduler] run_forever start interval_sec=%d learners=%d",
            interval_sec,
            len(self.learners),
        )
        while True:
            await self.run_once_async()
            await asyncio.sleep(interval_sec)


__all__ = [
    "DEFAULT_INTERVAL_SEC",
    "LEARNER_PERSISTENT_FAILURE_THRESHOLD",
    "LearnerScheduler",
    "TuneCycleReport",
]
