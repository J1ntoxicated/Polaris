"""Periodic data-stream retention producer for the live DEMO/PAPER loop.

The retention deletes (``polaris.storage.retention``) existed but NOTHING in the
running loop ever called them, so the raw/history streams grew without bound
(live: data/ 12 GB, bars 4.4 M rows, watchlist_focus 278 k rows) and the
observe-only probe sidecar grew to 2.2 GB. This producer is the missing wiring:
an async task that, every ``RETENTION_INTERVAL_SEC``, OFFLOADS the prune to a
worker thread (``asyncio.to_thread``) so the event loop is never blocked
([[feedback_db_lock_is_architecture_signal]] — the STALL fix pattern).

Two safety properties carried from the retention module:

* LEDGER UNTOUCHABLE — the live-DB prune goes through ``run_retention_live``,
  which only deletes the frozen ``RETENTION_SPEC`` allowlist (bars /
  ticker_baseline_samples / watchlist_focus / gate_events). positions / fills /
  signals / orders and every learner/cell/risk state table are ABSENT from the
  spec → no DELETE can ever reach them.
* DEGRADE-NEVER-CRASH — both prunes are wrapped to return ``{}`` on any sqlite
  error; the worker thread is further guarded so a thrown error can never kill
  the task or the loop. WAL hygiene stays with the loop's PASSIVE checkpoint
  producer — this task takes NO exclusive lock.

DEDICATED CONNECTIONS: the prune runs on its OWN live-DB + probe-DB handles
(opened lazily on the worker thread, closed on stop), never the loop-owned
``conn`` — so the loop's connection is never touched from two threads.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from polaris.core.probes.tuning_log import open_probe_db
from polaris.storage.retention import run_probe_retention, run_retention_live
from polaris.storage.schema import connect

logger = logging.getLogger(__name__)

# How often the live loop prunes. 30 min keeps the streams bounded between the
# daily down-window job WITHOUT churning the writer — each pass deletes only the
# rows that aged past the window since the last pass (idempotent, ~0 on a quiet
# table). NOT a hot-path cadence; the deletes are off-loop on a worker thread.
RETENTION_INTERVAL_SEC: float = 1800.0


def _prune_blocking(live_db: Path, probe_db: Path) -> None:
    """Prune both DBs on dedicated connections (runs on a worker thread).

    Opens, prunes, and closes its OWN handles each pass so no connection is
    shared across threads and nothing is left open between passes. Fully
    fail-open: a failure on either DB is logged and swallowed (the other DB and
    the next pass are unaffected). The probe DB is optional — a missing file is
    created by ``open_probe_db`` but a hard open error just skips the probe prune.
    """
    try:
        conn = connect(live_db)
        try:
            deleted = run_retention_live(conn)
        finally:
            conn.close()
        if any(deleted.values()):
            logger.info("[retention] live prune: %s", deleted)
    except Exception as exc:  # noqa: BLE001 — hygiene must never halt the loop
        logger.warning("[retention] live prune pass failed (degrade): %r", exc)

    try:
        pconn = open_probe_db(probe_db)
        try:
            pdeleted = run_probe_retention(pconn)
        finally:
            pconn.close()
        if any(pdeleted.values()):
            logger.info("[retention] probe prune: %s", pdeleted)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[retention] probe prune pass failed (degrade): %r", exc)
    except Exception as exc:  # noqa: BLE001 — hygiene must never halt the loop
        logger.warning("[retention] probe prune unexpected error (degrade): %r", exc)


async def retention_producer(
    *,
    live_db: Path,
    probe_db: Path,
    stop_evt: asyncio.Event,
    interval_sec: float = RETENTION_INTERVAL_SEC,
) -> None:
    """Periodically off-load the retention prune until ``stop_evt`` is set.

    Waits ``interval_sec`` between passes (interruptible by ``stop_evt`` for a
    prompt teardown) and offloads ``_prune_blocking`` to a worker thread so the
    event loop never blocks on the DELETE. The first pass runs after the first
    interval, not at boot (boot already does enough I/O; nothing is urgent).
    """
    while not stop_evt.is_set():
        try:
            await asyncio.wait_for(stop_evt.wait(), timeout=interval_sec)
            return  # stopped
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(_prune_blocking, live_db, probe_db)
        except Exception:  # noqa: BLE001 — task must survive any prune failure
            logger.debug("[retention] producer cycle failed", exc_info=True)
