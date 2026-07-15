"""Periodic retention producer — the missing live-loop wiring.

Validates the wiring that was absent (retention code existed but was never
called from the running loop → unbounded growth):

* the producer prunes BOTH the live DB and the probe sidecar on each pass;
* the live-DB ledger (positions/fills/signals) is NEVER touched;
* the producer tears down promptly on stop_evt;
* a broken probe DB degrades (live prune still runs, no crash).

DB-WRITER MIGRATION (forensic hunt 2026-07-09 — the live prune's own dedicated
autocommit conn was the ``database is locked`` storm culprit,
[[feedback_db_lock_is_architecture_signal]]): the remaining tests validate the
live-DB prune now routes through the shared ``DBWriter`` (chunked jobs) when
wired, falls back byte-identically to the pre-migration dedicated-conn path on
the kill switch, and — the TDD success criterion — that a concurrent
hot-path write load + the chunked retention prune produce ZERO
``database is locked`` errors through db_writer, with a control-group test
proving the OLD dedicated-conn path COULD reproduce that contention.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from polaris.core.probes.tuning_log import open_probe_db
from polaris.scripts._production_retention import (
    _prune_blocking,
    _prune_live_blocking,
    _prune_live_chunked,
    retention_producer,
)
from polaris.storage.db_writer import DBWriter
from polaris.storage.retention import RETENTION_SPEC
from polaris.storage.schema import connect, init_db

NOW = 1_782_000_000


def _seed_live(db_path: Path) -> None:
    conn = init_db(db_path)
    try:
        # Old 1m bar (outside the NIT-A 30d intraday window) → pruned; recent kept.
        # 1m (not 1D): the deep 1D/4H canvas now keeps a 1200d window, so only the
        # dense intraday streams prune on this timescale.
        for ts in (NOW - 40 * 86_400, NOW):
            conn.execute(
                "INSERT INTO bars (instrument_id, underlying_group_id, venue, "
                "symbol, bar_interval, ts, open, high, low, close, volume) "
                "VALUES ('I','G','okx','BTC-USDT','1m',?,1,1,1,1,1)",
                (ts,),
            )
        # Ledger rows with ANCIENT ts — must survive (allowlist protection).
        conn.execute(
            "INSERT INTO positions (position_id, venue, symbol, strategy_id, "
            "entry_strategy_id, active_strategy_id, side, qty, status, opened_ts) "
            "VALUES ('p1','okx','BTC-USDT','s','s','s','long',1,'closed',1)"
        )
        conn.execute(
            "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
            "size_usd, fill_price, ts_ms, order_id) "
            "VALUES ('f1','okx','I','s','buy',1,1,1000,'o1')"
        )
        conn.execute(
            "INSERT INTO signals (strategy_id, signal_id, instrument_id, direction, "
            "score, thesis, ts) VALUES ('s','sig1','I','long',1,'t',1)"
        )
        conn.commit()
    finally:
        conn.close()


def _seed_probe(db_path: Path) -> None:
    conn = open_probe_db(db_path)
    try:
        conn.execute(
            "INSERT INTO probe_decisions (decision_id, eval_id, ts, run_id, "
            "position_id, mode, composite_lean, action) "
            "VALUES ('d-old','e1',?,'run','p','observe',0.0,'hold')",
            (NOW - 60 * 86_400,),  # outside 45d → pruned
        )
        conn.execute(
            "INSERT INTO probe_decisions (decision_id, eval_id, ts, run_id, "
            "position_id, mode, composite_lean, action) "
            "VALUES ('d-new','e2',?,'run','p','observe',0.0,'hold')",
            (NOW,),  # kept
        )
    finally:
        conn.close()


def _count(db_path: Path, table: str) -> int:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table}"  # noqa: S608 test-local
        ).fetchone()[0]
    finally:
        conn.close()


def test_prune_blocking_prunes_streams_and_keeps_ledger(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite"
    probe = tmp_path / "probes.sqlite"
    _seed_live(live)
    _seed_probe(probe)

    # storage-split: bars is marketdata-domain — md_db=live (same combined
    # seeded DB) so both the trading and marketdata passes prune it.
    _prune_blocking(live, live, probe)

    # Stream pruned to the in-window rows.
    assert _count(live, "bars") == 1
    assert _count(probe, "probe_decisions") == 1
    # Ledger fully preserved.
    assert _count(live, "positions") == 1
    assert _count(live, "fills") == 1
    assert _count(live, "signals") == 1


def test_prune_blocking_degrades_on_missing_live_db(tmp_path: Path) -> None:
    """A live DB with no schema (no tables) must not raise — probe prune still
    runs. run_retention_live swallows the missing-table sqlite error."""
    live = tmp_path / "empty.sqlite"
    probe = tmp_path / "probes.sqlite"
    # Touch an empty (schema-less) live DB.
    import sqlite3

    sqlite3.connect(str(live)).close()
    _seed_probe(probe)

    _prune_blocking(live, live, probe)  # must not raise

    assert _count(probe, "probe_decisions") == 1


@pytest.mark.asyncio
async def test_retention_producer_runs_then_stops(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite"
    probe = tmp_path / "probes.sqlite"
    _seed_live(live)
    _seed_probe(probe)

    stop = asyncio.Event()
    # Tiny interval so the first pass fires immediately; then stop.
    task = asyncio.create_task(
        retention_producer(
            live_db=live, probe_db=probe, stop_evt=stop, interval_sec=0.01,
            # storage-split: bars is marketdata-domain — this test seeds a
            # single combined DB (init_db's unchanged full schema), so
            # md_db=live exercises the marketdata pass against the SAME file.
            md_db=live,
        )
    )
    # Let at least one prune pass complete.
    for _ in range(200):
        await asyncio.sleep(0.01)
        if _count(live, "bars") == 1:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert _count(live, "bars") == 1            # old bar pruned
    assert _count(live, "positions") == 1       # ledger intact
    assert _count(probe, "probe_decisions") == 1


@pytest.mark.asyncio
async def test_retention_producer_stops_promptly_without_pass(tmp_path: Path) -> None:
    """stop_evt set before any interval elapses → task returns at once."""
    live = tmp_path / "live.sqlite"
    probe = tmp_path / "probes.sqlite"
    _seed_live(live)
    _seed_probe(probe)

    stop = asyncio.Event()
    stop.set()
    task = asyncio.create_task(
        retention_producer(
            live_db=live, probe_db=probe, stop_evt=stop, interval_sec=9999.0
        )
    )
    await asyncio.wait_for(task, timeout=1.0)
    # No pass ran — the old bar is still present.
    assert _count(live, "bars") == 2


# ---------------------------------------------------------------------------
# db_writer-routed live prune (migration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_live_chunked_prunes_and_keeps_ledger(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite"
    _seed_live(live)
    dbw = DBWriter(live, batch_max=8, drain_ms=10)
    dbw.start()
    try:
        # storage-split: default spec is trading-only (gate_events); this
        # test also checks the (marketdata-domain) bars prune, so pass the
        # full combined spec explicitly against the single seeded DB.
        await _prune_live_chunked(dbw, now_ts=NOW, spec=RETENTION_SPEC)
    finally:
        dbw.stop()

    assert _count(live, "bars") == 1
    assert _count(live, "positions") == 1
    assert _count(live, "fills") == 1
    assert _count(live, "signals") == 1
    assert dbw.jobs_failed == 0


@pytest.mark.asyncio
async def test_prune_live_chunked_drains_across_multiple_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tiny chunk size forces one rule through several chunk jobs to drain."""
    monkeypatch.setenv("POLARIS_RETENTION_CHUNK_ROWS", "3")
    live = tmp_path / "live.sqlite"
    conn = init_db(live)
    try:
        for i in range(10):
            conn.execute(
                "INSERT INTO gate_events (event_id, run_id, gate_id, phase, "
                "created_ts) VALUES (?, 'run', 1, 'eval', ?)",
                (f"ev-{i}", NOW - 40 * 86_400 - i),  # all outside 30d
            )
        conn.commit()
    finally:
        conn.close()
    dbw = DBWriter(live, batch_max=8, drain_ms=10)
    dbw.start()
    try:
        await _prune_live_chunked(dbw, now_ts=NOW)
    finally:
        dbw.stop()

    assert _count(live, "gate_events") == 0
    # 10 rows / chunk_rows=3 -> at least 4 chunk jobs for this rule alone.
    assert dbw.jobs_processed >= 4


@pytest.mark.asyncio
async def test_retention_producer_routes_live_prune_through_db_writer(
    tmp_path: Path,
) -> None:
    live = tmp_path / "live.sqlite"
    probe = tmp_path / "probes.sqlite"
    _seed_live(live)
    _seed_probe(probe)
    dbw = DBWriter(live, batch_max=8, drain_ms=10)
    dbw.start()

    stop = asyncio.Event()
    task = asyncio.create_task(
        retention_producer(
            live_db=live, probe_db=probe, stop_evt=stop, interval_sec=0.01,
            db_writer=dbw,
            # storage-split: same single-DB precedent as above — route the
            # marketdata pass at the SAME file/writer so bars still prunes.
            md_db=live, md_db_writer=dbw,
        )
    )
    for _ in range(200):
        await asyncio.sleep(0.01)
        if _count(live, "bars") == 1:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    dbw.stop()

    assert _count(live, "bars") == 1
    assert _count(live, "positions") == 1
    assert _count(probe, "probe_decisions") == 1
    assert dbw.jobs_processed > 0  # the live prune went THROUGH db_writer


@pytest.mark.asyncio
async def test_retention_producer_kill_switch_falls_back_to_dedicated_conn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POLARIS_DBWRITER_ENABLED=0 -> legacy dedicated-conn path, byte-identical
    functional result, but NOTHING submitted to db_writer."""
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    live = tmp_path / "live.sqlite"
    probe = tmp_path / "probes.sqlite"
    _seed_live(live)
    _seed_probe(probe)
    dbw = DBWriter(live, batch_max=8, drain_ms=10)
    dbw.start()

    stop = asyncio.Event()
    task = asyncio.create_task(
        retention_producer(
            live_db=live, probe_db=probe, stop_evt=stop, interval_sec=0.01,
            db_writer=dbw,
            # storage-split: same single-DB precedent as above.
            md_db=live, md_db_writer=dbw,
        )
    )
    for _ in range(200):
        await asyncio.sleep(0.01)
        if _count(live, "bars") == 1:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    dbw.stop()

    assert _count(live, "bars") == 1  # prune still happened (legacy path)...
    assert dbw.jobs_processed == 0    # ...but never went through db_writer


# ---------------------------------------------------------------------------
# TDD success criterion: culprit write load + db_writer concurrent submit
# -> lock 0, with a control-group regression guard on the OLD path.
# ---------------------------------------------------------------------------


def _seed_many_aged_gate_events(db_path: Path, *, n: int) -> None:
    conn = init_db(db_path)
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO gate_events (event_id, run_id, gate_id, phase, "
                "created_ts) VALUES (?, 'run', 1, 'eval', ?)",
                (f"ev-{i}", NOW - 40 * 86_400 - i),  # outside the 30d window
            )
        conn.commit()
    finally:
        conn.close()


def test_control_group_retention_dedicated_conn_reproduces_lock_errors(
    tmp_path: Path,
) -> None:
    """Regression guard: the OLD retention path (its own dedicated autocommit
    conn issuing back-to-back DELETEs) contends a concurrent writer's WAL
    lock — the exact shape the forensic hunt pinned as the ``database is
    locked`` storm culprit (loop conn / focus_conn / QuoteTickWriter /
    TechnicalStoreWriter all hold their own conns and would see this same
    contention against the OLD path). ``busy_timeout=0`` on the concurrent
    writer surfaces any overlap immediately and deterministically."""
    live = tmp_path / "control.sqlite"
    _seed_many_aged_gate_events(live, n=3000)

    errors: list[Exception] = []
    errors_lock = threading.Lock()
    stop_hotpath = threading.Event()

    def hotpath_worker(thread_id: int) -> None:
        conn = sqlite3.connect(str(live), timeout=0, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=0;")
        try:
            seq = 0
            while not stop_hotpath.is_set():
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        "INSERT INTO signals (strategy_id, signal_id, "
                        "instrument_id, direction, score, thesis, ts) "
                        "VALUES ('s', ?, 'I', 'long', 1, 't', 1)",
                        (f"sig-{thread_id}-{seq}",),
                    )
                    conn.execute("COMMIT")
                except sqlite3.OperationalError as exc:
                    with errors_lock:
                        errors.append(exc)
                    with contextlib.suppress(sqlite3.Error):
                        conn.execute("ROLLBACK")
                seq += 1
        finally:
            conn.close()

    threads = [
        threading.Thread(target=hotpath_worker, args=(i,)) for i in range(6)
    ]
    for th in threads:
        th.start()

    _prune_live_blocking(live)  # OLD dedicated-conn path (culprit shape)

    stop_hotpath.set()
    for th in threads:
        th.join(timeout=10)

    assert errors, "control group expected to reproduce database-is-locked contention"


@pytest.mark.asyncio
async def test_dbwriter_retention_plus_hotpath_zero_lock_errors(
    tmp_path: Path,
) -> None:
    """THE FIX: retention's chunked db_writer prune + concurrent hot-path
    db_writer submits + a residual un-migrated conn (loop/focus_conn analog,
    standard connect() busy_timeout) all running concurrently -> ZERO
    ``database is locked`` errors anywhere."""
    live = tmp_path / "writer.sqlite"
    _seed_many_aged_gate_events(live, n=3000)

    dbw = DBWriter(live, batch_max=32, drain_ms=10, queue_max=8192, ckpt_sec=3600)
    dbw.start()

    stop_hotpath = threading.Event()
    residual_errors: list[Exception] = []
    residual_errors_lock = threading.Lock()

    def hotpath_worker(thread_id: int) -> None:
        """Simulates quote/tech-store 1Hz-like writers, now on db_writer. A
        tiny pace (still far faster than the real 1Hz cadence) keeps this a
        stress load without turning it into an unthrottled Python busy-loop
        that would overflow ``queue_max`` on submit volume alone — the
        property under test is WAL lock contention, not queue capacity."""
        seq = 0
        while not stop_hotpath.is_set():
            def _job(
                conn: sqlite3.Connection, t: int = thread_id, s: int = seq
            ) -> None:
                conn.execute(
                    "INSERT INTO signals (strategy_id, signal_id, "
                    "instrument_id, direction, score, thesis, ts) "
                    "VALUES ('s', ?, 'I', 'long', 1, 't', 1)",
                    (f"hp-{t}-{s}",),
                )
            dbw.submit(_job)
            seq += 1
            time.sleep(0.001)

    def residual_writer_worker() -> None:
        """Simulates the STILL-un-migrated loop conn / focus_conn — its own
        RW conn via the standard connect() (default busy_timeout), writing
        concurrently with db_writer's traffic. Proves step-3 (absorb any
        residual contention via the existing busy_timeout convention, no new
        hardcoded number)."""
        conn = connect(live)
        try:
            seq = 0
            while not stop_hotpath.is_set():
                try:
                    conn.execute(
                        "INSERT INTO watchlist_focus (cycle_ts, venue, symbol, "
                        "focus_score, focus_rank, target_bucket) VALUES "
                        "(?, 'okx', 'BTC-USDT', 1.0, 1, 'core')",
                        (NOW + seq,),
                    )
                except sqlite3.OperationalError as exc:
                    with residual_errors_lock:
                        residual_errors.append(exc)
                seq += 1
        finally:
            conn.close()

    threads = [
        threading.Thread(target=hotpath_worker, args=(i,)) for i in range(4)
    ] + [threading.Thread(target=residual_writer_worker)]
    for th in threads:
        th.start()

    # Retry passes (idempotent — mirrors the real 30min-cadence retry) so a
    # chunk dropped by momentary queue-full backpressure under this stress
    # load (flow_not_block: never raise, degrade to next pass) still reaches
    # full drain within the test, without weakening the lock-0 assertion below.
    for _ in range(20):
        await _prune_live_chunked(dbw, now_ts=NOW)
        if _count(live, "gate_events") == 0:
            break

    stop_hotpath.set()
    for th in threads:
        th.join(timeout=10)
    dbw.stop()

    assert dbw.jobs_failed == 0
    assert dbw.batch_failures == 0
    assert residual_errors == [], (
        f"busy_timeout should absorb residual contention, got {residual_errors}"
    )
    assert _count(live, "gate_events") == 0  # retention fully drained via db_writer
