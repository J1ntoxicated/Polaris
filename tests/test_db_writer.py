"""DB writer/reader split — single-serialized RW connection (design SSOT:
``vault/50_research/db-writer-reader-split-design_2026-07-08.md``).

DEMO/PAPER only. temp DB fixtures only — never touches a live DB.

Root cause pinned by these tests: the bot process opening 6+ independent RW
sqlite connections (each doing its own ``BEGIN..COMMIT``) contends WAL's single
writer lock and produces ``database is locked`` storms. §7 test plan:

1. concurrent write load -> lock 0 through DBWriter, with a control group
   (multiple direct RW conns against the same DB) reproducing lock errors > 0
   — a regression guard proving the harness CAN detect the problem.
2. reader (``connect_ro``) is unaffected by concurrent DBWriter load.
3. periodic TRUNCATE checkpoint reclaims -wal growth (creep bound).
4. graceful stop: final drain loses no submitted durable job.
5. hypothesis property: per-job atomicity (a failing job's partial writes never
   appear) + queue-full drops flow (never crashes the writer thread).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from concurrent.futures import Future

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from polaris.storage.db_writer import DBWriter, dbwriter_enabled
from polaris.storage.schema import connect, connect_ro

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


def _make_table(db_path: object) -> None:
    conn = connect(db_path)  # type: ignore[arg-type]
    conn.execute(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, thread INTEGER, seq INTEGER)"
    )
    conn.close()


# ---------------------------------------------------------------------------
# 0. kill switch
# ---------------------------------------------------------------------------


def test_dbwriter_enabled_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_DBWRITER_ENABLED", raising=False)
    assert dbwriter_enabled() is True


def test_dbwriter_enabled_env_zero_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    assert dbwriter_enabled() is False


# ---------------------------------------------------------------------------
# 1. concurrent write load -> lock 0 (+ control-group regression guard)
# ---------------------------------------------------------------------------


def test_control_group_multi_conn_reproduces_lock_errors(tmp_path: object) -> None:
    """Regression guard: the harness itself can detect self-contention.

    Multiple independent RW connections (the legacy per-writer dedicated-conn
    shape this design replaces), each with busy_timeout=0 (immediate fail
    instead of a 5s wait) so contention surfaces fast and deterministically.
    """
    db = tmp_path / "control.sqlite"  # type: ignore[operator]
    _make_table(db)
    errors: list[Exception] = []
    errors_lock = threading.Lock()
    n_threads = 8
    iters = 40

    def worker(thread_id: int) -> None:
        # DB is already WAL (set by _make_table's connect()) — no per-worker
        # journal_mode PRAGMA needed (that write-ish PRAGMA can itself race).
        conn = sqlite3.connect(str(db), timeout=0, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=0;")
            for seq in range(iters):
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        "INSERT INTO t (thread, seq) VALUES (?, ?)", (thread_id, seq)
                    )
                    conn.execute("COMMIT")
                except sqlite3.OperationalError as exc:
                    with errors_lock:
                        errors.append(exc)
                    with pytest_suppress():
                        conn.execute("ROLLBACK")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    assert any("lock" in str(e).lower() or "busy" in str(e).lower() for e in errors), (
        "control group expected to reproduce database-is-locked contention"
    )


def test_dbwriter_concurrent_submit_zero_lock_errors(tmp_path: object) -> None:
    """The DBWriter path: same concurrent workload, routed through submit().

    Only ONE RW connection ever exists (the writer thread's), so self-
    contention is structurally impossible — assert 0 job failures and every
    submitted row lands.
    """
    db = tmp_path / "writer.sqlite"  # type: ignore[operator]
    _make_table(db)
    w = DBWriter(db, batch_max=32, drain_ms=10, queue_max=8192, ckpt_sec=3600)
    w.start()
    n_threads = 8
    iters = 40

    def worker(thread_id: int) -> None:
        for seq in range(iters):
            def _job(conn: sqlite3.Connection, t: int = thread_id, s: int = seq) -> None:
                conn.execute(
                    "INSERT INTO t (thread, seq) VALUES (?, ?)", (t, s)
                )
            w.submit(_job)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    _drain_until(w, expected=n_threads * iters, timeout=15.0)
    w.stop()

    assert w.jobs_failed == 0
    assert w.batch_failures == 0
    ro = connect_ro(db)
    count = ro.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    ro.close()
    assert count == n_threads * iters


# ---------------------------------------------------------------------------
# 2. reader unaffected under concurrent write load
# ---------------------------------------------------------------------------


def test_reader_walks_succeed_under_write_load(tmp_path: object) -> None:
    db = tmp_path / "reader.sqlite"  # type: ignore[operator]
    _make_table(db)
    w = DBWriter(db, batch_max=16, drain_ms=5, ckpt_sec=0.2, ckpt_wal_pages=1)
    w.start()
    stop_writing = threading.Event()

    def writer_thread() -> None:
        seq = 0
        while not stop_writing.is_set():
            def _job(conn: sqlite3.Connection, s: int = seq) -> None:
                conn.execute("INSERT INTO t (thread, seq) VALUES (0, ?)", (s,))
            w.submit(_job)
            seq += 1
            time.sleep(0.001)

    wt = threading.Thread(target=writer_thread)
    wt.start()

    reader_errors: list[Exception] = []
    for _ in range(30):
        try:
            ro = connect_ro(db)
            ro.execute("SELECT COUNT(*) FROM t").fetchone()
            ro.close()
        except sqlite3.OperationalError as exc:
            reader_errors.append(exc)
        time.sleep(0.02)

    stop_writing.set()
    wt.join(timeout=10)
    w.stop()
    assert reader_errors == []


# ---------------------------------------------------------------------------
# 3. checkpoint behaviour — TRUNCATE reclaims -wal growth
# ---------------------------------------------------------------------------


def test_truncate_checkpoint_reclaims_wal(tmp_path: object) -> None:
    db = tmp_path / "ckpt.sqlite"  # type: ignore[operator]
    _make_table(db)
    w = DBWriter(db, batch_max=1, drain_ms=1, ckpt_sec=3600, ckpt_wal_pages=5)
    w.start()
    payload = "x" * 4096
    conn = connect(db)
    conn.execute("ALTER TABLE t ADD COLUMN blob TEXT")
    conn.close()
    for i in range(200):
        def _job(conn: sqlite3.Connection, i: int = i) -> None:
            conn.execute(
                "INSERT INTO t (thread, seq, blob) VALUES (0, ?, ?)", (i, payload)
            )
        fut = w.submit(_job, durable=True)
        assert fut is not None
        fut.result(timeout=5)
    # Size-triggered checkpoint should have fired at least once by now.
    _wait_until(lambda: w.checkpoints_truncated > 0, timeout=10.0)
    w.stop()
    assert w.checkpoints_truncated > 0


# ---------------------------------------------------------------------------
# 4. graceful stop — final drain loses no durable job
# ---------------------------------------------------------------------------


def test_graceful_stop_final_drain_no_loss(tmp_path: object) -> None:
    db = tmp_path / "drain.sqlite"  # type: ignore[operator]
    _make_table(db)
    w = DBWriter(db, batch_max=4, drain_ms=50)
    w.start()
    futures: list[Future[None]] = []
    for i in range(50):
        def _job(conn: sqlite3.Connection, i: int = i) -> None:
            conn.execute("INSERT INTO t (thread, seq) VALUES (1, ?)", (i,))
        fut = w.submit(_job, durable=True)
        assert fut is not None
        futures.append(fut)
    # Stop immediately (no sleep) — the final drain inside stop() must still
    # process every job already queued.
    w.stop(timeout=10)
    for fut in futures:
        fut.result(timeout=1)  # raises if any future was left unresolved
    conn = connect(db)
    count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    conn.close()
    assert count == 50


def test_stop_before_start_is_safe(tmp_path: object) -> None:
    db = tmp_path / "nostart.sqlite"  # type: ignore[operator]
    w = DBWriter(db)
    w.stop()  # must not raise


# ---------------------------------------------------------------------------
# 5. hypothesis property — per-job atomicity + queue-full drop-not-crash
# ---------------------------------------------------------------------------


@given(
    outcomes=st.lists(st.booleans(), min_size=1, max_size=25),
)
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_job_atomicity_failing_job_never_partially_visible(
    tmp_path_factory: pytest.TempPathFactory, outcomes: list[bool]
) -> None:
    """A job that raises mid-write must leave NO trace (SAVEPOINT rollback);
    a succeeding job's rows always land — regardless of interleave order."""
    db = tmp_path_factory.mktemp("hyp") / "atomic.sqlite"
    _make_table(db)
    w = DBWriter(db, batch_max=100, drain_ms=20)
    w.start()
    try:
        futures: list[tuple[int, bool, Future[None]]] = []
        for i, should_succeed in enumerate(outcomes):
            def _job(conn: sqlite3.Connection, i: int = i, ok: bool = should_succeed) -> None:
                conn.execute("INSERT INTO t (thread, seq) VALUES (99, ?)", (i,))
                if not ok:
                    raise ValueError("intentional failure — must roll back this job only")
            fut = w.submit(_job, durable=True)
            assert fut is not None
            futures.append((i, should_succeed, fut))
        for _i, should_succeed, fut in futures:
            if should_succeed:
                fut.result(timeout=5)
            else:
                with pytest.raises(ValueError):
                    fut.result(timeout=5)
        conn = connect(db)
        rows = {r[0] for r in conn.execute("SELECT seq FROM t WHERE thread = 99")}
        conn.close()
        expected = {i for i, ok, _ in futures if ok}
        assert rows == expected
    finally:
        w.stop()


def test_queue_full_drops_never_crashes(tmp_path: object) -> None:
    db = tmp_path / "full.sqlite"  # type: ignore[operator]
    _make_table(db)
    # Tiny queue + a job that blocks the writer thread so the queue backs up.
    release = threading.Event()

    def _blocker(conn: sqlite3.Connection) -> None:
        release.wait(timeout=5)

    w = DBWriter(db, batch_max=1, drain_ms=1, queue_max=2)
    w.start()
    w.submit(_blocker)  # occupies the writer thread
    # Give the writer a moment to pick up the blocker job.
    time.sleep(0.1)
    dropped_any = False
    for _ in range(20):
        fut = w.submit(lambda conn: None, durable=True)
        if fut is not None and fut.done() and fut.exception() is not None:
            dropped_any = True
    release.set()
    w.stop(timeout=10)
    assert dropped_any
    assert w.jobs_dropped > 0


# ---------------------------------------------------------------------------
# 6. batch-commit timing instrumentation (writer-migration-completion design
#    §"Per-file change spec" #1 — pure observability, fills the unmeasured
#    batch-hold-time gap the busy_timeout sizing decision depends on).
# ---------------------------------------------------------------------------


def test_commit_batch_logs_duration_and_tracks_max(
    tmp_path: object, caplog: pytest.LogCaptureFixture,
) -> None:
    db = tmp_path / "timing.sqlite"  # type: ignore[operator]
    _make_table(db)
    w = DBWriter(db, batch_max=8, drain_ms=5)
    w.start()
    caplog.set_level(logging.DEBUG, logger="polaris.storage.db_writer")
    n_jobs = 5
    futures: list[Future[None]] = []
    for i in range(n_jobs):
        def _job(conn: sqlite3.Connection, i: int = i) -> None:
            conn.execute("INSERT INTO t (thread, seq) VALUES (0, ?)", (i,))
        fut = w.submit(_job, durable=True)
        assert fut is not None
        futures.append(fut)
    for fut in futures:
        fut.result(timeout=5)  # regression: futures still resolve post-COMMIT
    w.stop()

    # regression: counter behaviour unchanged by the added instrumentation.
    assert w.batches_committed >= 1
    assert w.batch_failures == 0

    duration_records = [
        r for r in caplog.records
        if r.name == "polaris.storage.db_writer"
        and r.getMessage().startswith("[db_writer] batch ")
    ]
    assert duration_records, "expected a post-COMMIT batch duration debug log"
    total_jobs_logged = 0
    for rec in duration_records:
        msg = rec.getMessage()
        # "[db_writer] batch <n> jobs <ms>ms"
        parts = msg.split()
        assert parts[3] == "jobs"
        total_jobs_logged += int(parts[2])
        assert parts[4].endswith("ms")
        float(parts[4][:-2])  # duration parses as a float
    assert total_jobs_logged == n_jobs
    assert w.batch_commit_ms_max >= 0.0


# ---------------------------------------------------------------------------
# 7. adversarial — db_writer batch load + 1 direct writer under contention.
#    busy_timeout must arbitrate: the direct writer SUCCEEDS (retries within
#    the timeout), never raises OperationalError. A failure here means the
#    collision class is immediate SQLITE_BUSY_SNAPSHOT (timeout can't fix it)
#    and the design decision escalates to broader coalescing.
# ---------------------------------------------------------------------------


def test_direct_writer_survives_dbwriter_batch_contention(tmp_path: object) -> None:
    db = tmp_path / "contend.sqlite"  # type: ignore[operator]
    _make_table(db)
    w = DBWriter(db, batch_max=32, drain_ms=5, ckpt_sec=3600)
    w.start()
    stop_flooding = threading.Event()

    def _flood() -> None:
        seq = 0
        while not stop_flooding.is_set():
            def _job(conn: sqlite3.Connection, s: int = seq) -> None:
                conn.execute("INSERT INTO t (thread, seq) VALUES (1, ?)", (s,))
            w.submit(_job)
            seq += 1

    flooder = threading.Thread(target=_flood)
    flooder.start()
    try:
        # A direct RW connection outside DBWriter — same busy_timeout PRAGMA
        # path as loop/focus conns (connect()) per the design's structural
        # arbiter. Must NOT raise while DBWriter is mid-flood.
        direct = connect(db)
        errors: list[Exception] = []
        for i in range(20):
            try:
                direct.execute("BEGIN IMMEDIATE")
                direct.execute(
                    "INSERT INTO t (thread, seq) VALUES (2, ?)", (i,)
                )
                direct.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                errors.append(exc)
                with pytest_suppress():
                    direct.execute("ROLLBACK")
        direct.close()
    finally:
        stop_flooding.set()
        flooder.join(timeout=10)
        w.stop()
    assert errors == [], (
        f"direct writer hit OperationalError under busy_timeout arbitration: {errors}"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class pytest_suppress:
    def __enter__(self) -> pytest_suppress:
        return self

    def __exit__(self, *exc: object) -> bool:
        return True


def _wait_until(cond: object, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():  # type: ignore[operator]
            return
        time.sleep(0.02)
    assert cond(), "condition never became true within timeout"  # type: ignore[operator]


def _drain_until(w: DBWriter, *, expected: int, timeout: float) -> None:
    _wait_until(lambda: w.jobs_processed + w.jobs_failed >= expected, timeout=timeout)
