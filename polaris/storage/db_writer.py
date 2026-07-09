"""Single-serialized DB writer — the process-wide ONLY RW sqlite connection.

Design SSOT: ``vault/50_research/db-writer-reader-split-design_2026-07-08.md``.
DEMO/PAPER only. Root-cause fix for ``database is locked`` storms
([[feedback_db_lock_is_architecture_signal]]): the bot process used to open 6+
independent RW connections (loop ``conn``, ``focus_conn``, ``QuoteTickWriter``'s
own conn, ``TechnicalStoreWriter``'s own conn, per-call ingest offload conns,
...) that each ran their own ``BEGIN..COMMIT`` and competed for WAL's single
writer lock — the #74 offload pattern only moved that contention off the event
loop thread, it never removed it.

This module is the GENERALIZATION of #74: instead of one dedicated thread per
writer, every writer submits a job (a plain callable that takes the ONE shared
RW ``sqlite3.Connection`` and issues its statements — no BEGIN/COMMIT of its
own, the writer thread owns the transaction boundary) onto an MPSC queue drained
by a single background thread that holds the one RW connection for every WIRED
writer. Routing the high-frequency writers (ingest, quote flush, tech-store,
altdata, static/ticker ground) through a single serialized RW conn removes their
mutual WAL-write-lock self-contention, so ``database is locked`` drops sharply.
It is REDUCED, not eliminated: the loop ``conn``, ``focus_conn`` and
``probe_conn`` remain independent RW connections (not yet migrated), so residual
contention with those is bounded, not zero.

Batching: jobs that arrive within a short drain window are committed together
(one WAL frame flush for many jobs = throughput), but each job runs inside its
own ``SAVEPOINT`` so one job's failure rolls back ONLY that job — the rest of
the batch still commits (isolation without one bad write poisoning the batch).

Checkpointing: because this thread is the only writer, a ``TRUNCATE`` checkpoint
is now safe (the old PASSIVE-only policy existed because a self-competing writer
could deadlock behind its own TRUNCATE's exclusive lock — that hazard is gone
once there is exactly one writer). ``TRUNCATE`` only waits on READERS draining,
never on this writer, so it is attempted periodically to reclaim the ``-wal``
file instead of letting it creep unbounded between restarts.

flow_not_block / degrade-never-crash: a fire-and-forget job that cannot be
queued (queue full) or that fails during commit is DROPPED and counted, never
raised into the caller — the bot keeps trading. A caller that needs a durable
ack passes ``durable=True`` and gets a ``Future`` back.

Kill switch: ``POLARIS_DBWRITER_ENABLED`` (default ``1``). Callers check
``dbwriter_enabled()`` and fall back to their pre-existing dedicated-conn
behaviour when it reads ``0`` — a same-restart rollback with no code revert.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

from polaris.storage.schema import connect

__all__ = ["DBWriter", "dbwriter_enabled"]

logger = logging.getLogger(__name__)

# Internal scheduling granularity (how often the writer thread wakes with an
# empty queue to re-check stop + checkpoint due-ness). Not a caller-facing
# tunable — mirrors ``quote_writer``'s internal bucket constants.
_POLL_TIMEOUT_SEC = 0.5


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def dbwriter_enabled() -> bool:
    """Kill switch: ``POLARIS_DBWRITER_ENABLED=0`` reverts every wired caller to
    its pre-existing dedicated-conn behaviour (no code change needed to roll
    back — see module docstring)."""
    return os.environ.get("POLARIS_DBWRITER_ENABLED", "1") != "0"


@dataclass(slots=True)
class _Job:
    fn: Callable[[sqlite3.Connection], None]
    future: Future[None] | None
    label: str


class DBWriter:
    """The process's ONLY RW sqlite connection, fed by an MPSC job queue.

    Lifecycle::

        w = DBWriter(db_path)
        w.start()
        w.submit(lambda conn: conn.execute("INSERT ..."))   # fire-and-forget
        fut = w.submit(fn, durable=True); fut.result(timeout=5)  # durable ack
        ...
        w.stop()   # final drain + TRUNCATE checkpoint + close
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        batch_max: int | None = None,
        drain_ms: float | None = None,
        queue_max: int | None = None,
        ckpt_sec: float | None = None,
        ckpt_wal_pages: int | None = None,
    ) -> None:
        self._db_path = db_path
        self._batch_max = batch_max if batch_max is not None else _env_int(
            "POLARIS_DBWRITER_BATCH_MAX", 64
        )
        drain_ms_v = drain_ms if drain_ms is not None else _env_float(
            "POLARIS_DBWRITER_DRAIN_MS", 50.0
        )
        self._drain_sec = max(0.0, drain_ms_v) / 1000.0
        self._queue_max = queue_max if queue_max is not None else _env_int(
            "POLARIS_DBWRITER_QUEUE_MAX", 4096
        )
        self._ckpt_sec = ckpt_sec if ckpt_sec is not None else _env_float(
            "POLARIS_DBWRITER_CKPT_SEC", 15.0
        )
        self._ckpt_wal_pages = (
            ckpt_wal_pages if ckpt_wal_pages is not None
            else _env_int("POLARIS_DBWRITER_CKPT_WAL_PAGES", 4000)
        )
        self._queue: queue.Queue[_Job] = queue.Queue(maxsize=self._queue_max)
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn: sqlite3.Connection | None = None
        self._page_size = 4096
        self._last_ckpt_mono = 0.0

        # Observability counters (read by ops/tests — never gates behaviour).
        self.jobs_processed = 0
        self.jobs_dropped = 0
        self.jobs_failed = 0
        self.batches_committed = 0
        self.batch_failures = 0
        self.checkpoints_truncated = 0
        # Rolling max BEGIN->COMMIT hold time (ms) — instrumentation for the
        # busy_timeout sizing decision (writer-migration-completion design):
        # batch-hold time was previously unmeasured (counters only).
        self.batch_commit_ms_max = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the ONE RW connection and start the writer thread. Idempotent."""
        if self._thread is not None:
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="db-writer", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float = 10.0) -> None:
        """Signal stop and join the writer thread (final drain happens inside).

        Safe to call more than once / before ``start()``.
        """
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    # ------------------------------------------------------------------
    # Submission (thread-safe — callable from the event-loop thread directly,
    # since ``put_nowait`` never blocks on I/O; no executor hop needed).
    # ------------------------------------------------------------------

    def submit(
        self,
        fn: Callable[[sqlite3.Connection], None],
        *,
        durable: bool = False,
        label: str = "",
    ) -> Future[None] | None:
        """Enqueue a job. Fire-and-forget by default (flow_not_block): a full
        queue drops the job (counted) and returns ``None``/an already-failed
        Future rather than raising into the caller's hot path.

        ``fn`` receives the shared RW connection and must NOT call
        ``BEGIN``/``COMMIT``/``ROLLBACK`` itself — the writer thread owns the
        per-job ``SAVEPOINT`` and the per-batch transaction.
        """
        future: Future[None] | None = Future() if durable else None
        job = _Job(fn=fn, future=future, label=label)
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self.jobs_dropped += 1
            if future is not None:
                future.set_exception(
                    queue.Full(f"DBWriter queue full (max={self._queue_max})")
                )
        return future

    # ------------------------------------------------------------------
    # Writer thread body
    # ------------------------------------------------------------------

    def _run(self) -> None:
        self._conn = connect(self._db_path)
        with contextlib.suppress(sqlite3.Error):
            row = self._conn.execute("PRAGMA page_size").fetchone()
            if row:
                self._page_size = int(row[0])
        self._last_ckpt_mono = time.monotonic()
        try:
            while not self._stop_evt.is_set():
                batch = self._collect_batch(block=True)
                if batch:
                    self._commit_batch(batch)
                self._maybe_checkpoint()
            # Final drain: process whatever queued between the last empty poll
            # and the stop flag being observed — no submitted job is lost.
            while True:
                leftover = self._collect_batch(block=False)
                if not leftover:
                    break
                self._commit_batch(leftover)
        finally:
            with contextlib.suppress(sqlite3.Error):
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()

    def _collect_batch(self, *, block: bool) -> list[_Job]:
        batch: list[_Job] = []
        try:
            first = (
                self._queue.get(timeout=_POLL_TIMEOUT_SEC)
                if block else self._queue.get_nowait()
            )
        except queue.Empty:
            return batch
        batch.append(first)
        deadline = time.monotonic() + self._drain_sec
        while len(batch) < self._batch_max and time.monotonic() < deadline:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _commit_batch(self, batch: list[_Job]) -> None:
        # Futures are resolved ONLY after COMMIT actually lands (below) — never
        # inside the per-job loop. A job that raises inside a savepoint is
        # isolated (rolled back to its savepoint) but its SIBLINGS in the same
        # batch are still mid-transaction; resolving a future early would let a
        # caller observe "success" before the enclosing COMMIT is durable, and
        # would leave that future un-correctable if the WHOLE batch later fails
        # (a future can only be resolved once).
        conn = self._conn
        assert conn is not None
        start_mono = time.monotonic()
        try:
            conn.execute("BEGIN")
        except sqlite3.Error as exc:
            self._fail_batch(batch, exc)
            return
        outcomes: list[Exception | None] = []
        try:
            for idx, job in enumerate(batch):
                outcomes.append(self._run_job(conn, idx, job))
            conn.execute("COMMIT")
            self.batches_committed += 1
        except sqlite3.Error as exc:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            self._fail_batch(batch, exc)
            return
        # Pure observability (writer-migration-completion design #1): the
        # BEGIN->COMMIT hold time was previously unmeasured — this is the
        # evidence the busy_timeout sizing decision depends on. No behaviour
        # or counter semantics change.
        duration_ms = (time.monotonic() - start_mono) * 1000.0
        if duration_ms > self.batch_commit_ms_max:
            self.batch_commit_ms_max = duration_ms
        logger.debug("[db_writer] batch %d jobs %.1fms", len(batch), duration_ms)
        for job, err in zip(batch, outcomes, strict=True):
            if job.future is None:
                continue
            if err is not None:
                job.future.set_exception(err)
            else:
                job.future.set_result(None)

    def _run_job(
        self, conn: sqlite3.Connection, idx: int, job: _Job
    ) -> Exception | None:
        """Run one job inside its own SAVEPOINT. Returns the exception it raised
        (already isolated via ROLLBACK TO) or None on success — the caller
        resolves futures only once the enclosing batch COMMITs."""
        savepoint = f"dbw_{idx}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            job.fn(conn)
        except Exception as exc:  # noqa: BLE001 — one bad job never poisons the batch
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
            self.jobs_failed += 1
            logger.warning(
                "[db_writer] job %r failed (isolated rollback): %r",
                job.label or idx, exc,
            )
            return exc
        conn.execute(f"RELEASE {savepoint}")
        self.jobs_processed += 1
        return None

    def _fail_batch(self, batch: list[_Job], exc: Exception) -> None:
        # Whole-batch failure (e.g. BEGIN/COMMIT itself erred) — degrade the
        # entire batch, never raise into a producer thread (AGGRESSIVE/
        # flow_not_block: the bot keeps trading, the batch is simply lost).
        self.batch_failures += 1
        self.jobs_failed += len(batch)
        logger.warning(
            "[db_writer] batch commit failed, dropping %d job(s): %r",
            len(batch), exc,
        )
        for job in batch:
            if job.future is not None and not job.future.done():
                job.future.set_exception(exc)

    # ------------------------------------------------------------------
    # Checkpoint (TRUNCATE — safe because this thread is the ONLY writer)
    # ------------------------------------------------------------------

    def _maybe_checkpoint(self) -> None:
        now = time.monotonic()
        due_time = (now - self._last_ckpt_mono) >= self._ckpt_sec
        due_size = (not due_time) and self._wal_pages() > self._ckpt_wal_pages
        if not (due_time or due_size):
            return
        self._last_ckpt_mono = now
        conn = self._conn
        assert conn is not None
        try:
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.Error as exc:
            logger.debug("[db_writer] checkpoint attempt failed: %r", exc)
            return
        busy = row[0] if row else 1
        if not busy:
            self.checkpoints_truncated += 1
        else:
            logger.debug(
                "[db_writer] TRUNCATE checkpoint partial (reader draining): %r", row
            )

    def _wal_pages(self) -> int:
        try:
            size = Path(f"{self._db_path}-wal").stat().st_size
        except OSError:
            return 0
        return size // max(self._page_size, 1)
