"""Periodic data-stream retention producer for the live DEMO/PAPER loop.

The retention deletes (``polaris.storage.retention``) existed but NOTHING in the
running loop ever called them, so the raw/history streams grew without bound
(live: data/ 12 GB, bars 4.4 M rows, watchlist_focus 278 k rows) and the
observe-only probe sidecar grew to 2.2 GB. This producer is the missing wiring:
an async task that, every ``RETENTION_INTERVAL_SEC``, prunes the live DB.

Two safety properties carried from the retention module:

* LEDGER UNTOUCHABLE — the live-DB prune goes through ``run_retention_live`` /
  ``prune_table_chunk``, which only delete the frozen ``RETENTION_SPEC``
  allowlist (bars / ticker_baseline_samples / watchlist_focus / gate_events).
  positions / fills / signals / orders and every learner/cell/risk state table
  are ABSENT from the spec → no DELETE can ever reach them.
* DEGRADE-NEVER-CRASH — every prune path is wrapped to swallow sqlite errors;
  a failure can never kill this task or the loop.

LIVE-DB ROUTING (db_writer migration, forensic hunt 2026-07-09 — the live
prune's OWN dedicated autocommit conn was issuing 11 unwrapped DELETEs back to
back and monopolizing the WAL write-lock for 90-140s every ~30min, starving
the loop conn / focus_conn / QuoteTickWriter / TechnicalStoreWriter past their
``busy_timeout`` — [[feedback_db_lock_is_architecture_signal]]): when
``db_writer`` is wired and the kill switch (``POLARIS_DBWRITER_ENABLED``) is
enabled, the live prune routes through ``prune_table_chunk`` — each rule's
DELETE is capped to ``chunk_rows`` and durable-submitted to the shared
``DBWriter`` one chunk at a time, AWAITING each chunk's COMMIT before
submitting the next. This bounds every individual write-lock hold to one
chunk's worth of rows and lets the writer thread interleave hot-path jobs
(quote/tech-store/ingest/altdata/ground) between chunks instead of running 11
statements back to back inside one long transaction. ``db_writer=None`` (or
the kill switch off) falls back to the pre-migration dedicated-conn blocking
prune on a worker thread — byte-identical rollback, no code change needed.

PROBE DB: unconditionally pruned on its OWN dedicated conn (never migrated to
db_writer) — ``data/probes.sqlite`` is a SEPARATE file with its own WAL, so it
shares no write lock with the live DB's writers; the forensic hunt confirmed
it is a bystander, not a contender, in this lock storm.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from pathlib import Path

from polaris.core.probes.tuning_log import open_probe_db
from polaris.storage.db_writer import DBWriter, dbwriter_enabled
from polaris.storage.retention import (
    RETENTION_SPEC_MARKETDATA,
    RETENTION_SPEC_TRADING,
    RetentionRule,
    prune_table_chunk,
    run_probe_retention,
    run_retention_live,
)
from polaris.storage.schema import connect

logger = logging.getLogger(__name__)

# How often the live loop prunes. 30 min keeps the streams bounded between the
# daily down-window job WITHOUT churning the writer — each pass deletes only the
# rows that aged past the window since the last pass (idempotent, ~0 on a quiet
# table). NOT a hot-path cadence; the deletes are off-loop on a worker thread
# (legacy path) or chunked db_writer jobs (migrated path).
RETENTION_INTERVAL_SEC: float = 1800.0


def _retention_chunk_rows() -> int:
    """``POLARIS_RETENTION_CHUNK_ROWS`` env knob (default 2000, never hardcoded
    in the submit loop). Bigger than ``POLARIS_GROUND_CHUNK_ROWS`` (200) —
    retention passes typically age out far more rows per cycle than the
    ~1882-row ground walk, so a larger chunk keeps the chunk COUNT (and its
    per-chunk submit/await round-trip overhead) reasonable while still
    bounding any one chunk's write-lock hold well under ``busy_timeout``.
    """
    raw = os.environ.get("POLARIS_RETENTION_CHUNK_ROWS")
    if raw is None or not raw.strip():
        return 2000
    try:
        value = int(raw)
    except ValueError:
        return 2000
    return value if value > 0 else 2000


def _prune_live_blocking(
    live_db: Path, *, spec: tuple[RetentionRule, ...] = RETENTION_SPEC_TRADING,
) -> None:
    """Legacy path: prune a DB on a dedicated conn (own worker thread).

    Used only when ``db_writer`` is unavailable/kill-switched — byte-identical
    to the pre-migration behaviour (same allowlist deletes via
    ``run_retention_live``, same degrade-never-crash wrapping).

    ``spec`` (storage-split): defaults to the trading-domain rules
    (``gate_events``) for ``live_db``; the marketdata pass below passes
    ``RETENTION_SPEC_MARKETDATA`` against the marketdata DB path instead.
    """
    try:
        conn = connect(live_db)
        try:
            deleted = run_retention_live(conn, spec=spec)
        finally:
            conn.close()
        if any(deleted.values()):
            logger.info("[retention] prune %s: %s", live_db.name, deleted)
    except Exception as exc:  # noqa: BLE001 — hygiene must never halt the loop
        logger.warning("[retention] prune pass failed for %s (degrade): %r", live_db, exc)


def _prune_probe_blocking(probe_db: Path) -> None:
    """Prune the probe sidecar on a dedicated conn (own worker thread).

    NEVER routed through db_writer — probes.sqlite is a separate file with its
    own WAL, so it shares no write lock with the live DB's writers (see module
    docstring). Runs unconditionally, on every pass, regardless of the live-DB
    routing.
    """
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


def _prune_blocking(live_db: Path, md_db: Path, probe_db: Path) -> None:
    """Prune all three DBs on dedicated connections (runs on a worker thread).

    Back-compat wrapper over ``_prune_live_blocking`` (x2, one per domain) +
    ``_prune_probe_blocking`` — same shape as before this module's db_writer
    migration, extended for the storage-split marketdata DB. Fully fail-open:
    a failure on any DB is logged and swallowed independently.
    """
    _prune_live_blocking(live_db, spec=RETENTION_SPEC_TRADING)
    _prune_live_blocking(md_db, spec=RETENTION_SPEC_MARKETDATA)
    _prune_probe_blocking(probe_db)


async def _prune_rule_chunked(
    db_writer: DBWriter, rule: RetentionRule, *, now_ts: int, chunk_rows: int,
) -> int:
    """Durable-submit one retention rule's DELETE in bounded chunks.

    Awaits each chunk's COMMIT before submitting the next — so a rule with
    millions of aged rows (``bars``) is spread across many short db_writer
    jobs instead of one long-held write lock, and the writer thread is free to
    interleave hot-path jobs between chunks. Degrades (stops this rule for the
    current pass, next pass picks up where it left off — idempotent) on any
    submit/commit failure rather than raising into the producer.
    """
    total = 0
    while True:
        box: dict[str, int] = {"n": 0}

        def _job(
            conn: sqlite3.Connection, r: RetentionRule = rule, box: dict[str, int] = box,
        ) -> None:
            box["n"] = prune_table_chunk(
                conn, r.table, r.ts_column, r.retain_sec,
                now_ts=now_ts, chunk_rows=chunk_rows, bar_interval=r.bar_interval,
            )

        future = db_writer.submit(_job, durable=True, label=f"retention_{rule.table}")
        assert future is not None  # durable=True always returns a Future
        try:
            await asyncio.wrap_future(future)
        except Exception as exc:  # noqa: BLE001 — degrade this rule, next pass retries
            logger.debug(
                "[retention] chunk submit failed for %s (degrade): %r", rule.table, exc,
            )
            break
        n = box["n"]
        total += n
        if n < chunk_rows:
            break  # fully drained for this pass
    return total


async def _prune_live_chunked(
    db_writer: DBWriter,
    *,
    now_ts: int,
    spec: tuple[RetentionRule, ...] = RETENTION_SPEC_TRADING,
) -> None:
    """Chunked db_writer-routed prune — one rule at a time.

    ``spec`` (storage-split): defaults to the trading-domain rule
    (``gate_events``); the marketdata pass in ``retention_producer`` passes
    ``RETENTION_SPEC_MARKETDATA`` with the marketdata ``DBWriter`` instead.
    """
    chunk_rows = _retention_chunk_rows()
    deleted: dict[str, int] = {}
    for rule in spec:
        n = await _prune_rule_chunked(db_writer, rule, now_ts=now_ts, chunk_rows=chunk_rows)
        deleted[rule.table] = deleted.get(rule.table, 0) + n
    if any(deleted.values()):
        logger.info("[retention] live prune (db_writer chunked): %s", deleted)


async def retention_producer(
    *,
    live_db: Path,
    probe_db: Path,
    stop_evt: asyncio.Event,
    interval_sec: float = RETENTION_INTERVAL_SEC,
    db_writer: DBWriter | None = None,
    md_db: Path | None = None,
    md_db_writer: DBWriter | None = None,
) -> None:
    """Periodically off-load the retention prune until ``stop_evt`` is set.

    Waits ``interval_sec`` between passes (interruptible by ``stop_evt`` for a
    prompt teardown). The LIVE-DB (trading-domain, ``gate_events`` only)
    prune routes through ``db_writer`` (chunked jobs, see
    ``_prune_live_chunked``) when wired and the kill switch reads enabled;
    ``db_writer=None`` falls back to ``_prune_live_blocking`` on a worker
    thread (byte-identical to the pre-migration behaviour).

    storage-split: ``md_db``/``md_db_writer`` run the SAME pattern against
    the marketdata DB with ``RETENTION_SPEC_MARKETDATA`` (bars/baseline/
    focus/shadow/altdata — the actual firehose this split exists to bound).
    ``md_db=None`` (unwired caller/test) skips the marketdata pass entirely
    — byte-identical to the pre-split single-DB behaviour.

    The PROBE-DB prune is UNCONDITIONAL every pass, always on its own
    dedicated conn/worker-thread (see module docstring). The first pass runs
    after the first interval, not at boot (boot already does enough I/O;
    nothing here is urgent).
    """
    while not stop_evt.is_set():
        try:
            await asyncio.wait_for(stop_evt.wait(), timeout=interval_sec)
            return  # stopped
        except TimeoutError:
            pass
        try:
            if db_writer is not None and dbwriter_enabled():
                await _prune_live_chunked(
                    db_writer, now_ts=int(time.time()), spec=RETENTION_SPEC_TRADING
                )
            else:
                await asyncio.to_thread(
                    _prune_live_blocking, live_db, spec=RETENTION_SPEC_TRADING
                )
        except Exception:  # noqa: BLE001 — task must survive any prune failure
            logger.debug("[retention] producer live-prune cycle failed", exc_info=True)
        if md_db is not None:
            try:
                if md_db_writer is not None and dbwriter_enabled():
                    await _prune_live_chunked(
                        md_db_writer, now_ts=int(time.time()),
                        spec=RETENTION_SPEC_MARKETDATA,
                    )
                else:
                    await asyncio.to_thread(
                        _prune_live_blocking, md_db, spec=RETENTION_SPEC_MARKETDATA
                    )
            except Exception:  # noqa: BLE001 — task must survive any prune failure
                logger.debug(
                    "[retention] producer marketdata-prune cycle failed", exc_info=True
                )
        try:
            await asyncio.to_thread(_prune_probe_blocking, probe_db)
        except Exception:  # noqa: BLE001 — task must survive any prune failure
            logger.debug("[retention] producer probe-prune cycle failed", exc_info=True)
