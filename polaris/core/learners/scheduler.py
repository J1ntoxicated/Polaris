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

    def run_once(self, *, now_ts: int | None = None) -> TuneCycleReport:
        ts = int(now_ts if now_ts is not None else time.time())
        cycle = TuneCycleReport(cycle_ts=ts)
        logger.info(
            "[scheduler] tune cycle start ts=%d learners=%d",
            ts,
            len(self.learners),
        )
        for learner in self.learners:
            try:
                cycle.reports.append(learner.commit_hourly(now_ts=ts))
            except Exception as exc:  # noqa: BLE001 — capture for toggle
                # principle 4 — toggle on failure (caller can re-enable later).
                logger.error(
                    "[scheduler] learner %s commit_hourly failed: %r — disabling",
                    learner.learner_id,
                    exc,
                )
                learner.enabled = False
        cycle.expired_blocks = evict_expired_triple_blocks(self.conn, now_ts=ts)
        logger.info(
            "[scheduler] tune cycle end keys_updated_total=%d expired_blocks=%d",
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
            self.run_once()
            await asyncio.sleep(interval_sec)


__all__ = ["DEFAULT_INTERVAL_SEC", "LearnerScheduler", "TuneCycleReport"]
