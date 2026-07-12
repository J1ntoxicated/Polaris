"""tsmom_literature_shadow gate_shadow_events INSERT routed through the
shared DBWriter (db-writer-reader-split roadmap 1.10, 2026-07-12 audit item
[A] — called inside the ``dispatch_prefetch`` tick stage).

DEMO/PAPER only. temp DB only. Default (``db_writer=None``) stays
byte-identical to the pre-migration direct-write path — covered by
``test_tsmom_literature_shadow.py``. This file covers ONLY the new opt-in
path: (1) the submitted job issues the IDENTICAL SQL (and params, modulo the
random ``event_id``) as the direct-write branch, (2) an end-to-end real
DBWriter run lands an equivalent row, (3) the kill switch falls back to the
direct-conn path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

from polaris.core.pipeline.agents.tsmom_literature_shadow import (
    log_tsmom_literature_shadow,
)
from polaris.storage.db_writer import DBWriter
from polaris.storage.schema import ALL_DDL, connect, init_db
from polaris.strategies.base import BarView


def _mkconn(tmp_path: Path, name: str = "shadow.sqlite") -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / name, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    return conn


def _bars_from_closes(closes: list[float]) -> list[BarView]:
    return [
        BarView(ts=1_700_000_000 + i * 86400, open=c, high=c, low=c, close=c, volume=1.0)
        for i, c in enumerate(closes)
    ]


_CLOSES = [100.0] * 232 + [200.0] * 22  # positive literature momentum (warmup met)


class _RecordingConn:
    """Wraps a real sqlite3.Connection and logs every ``execute`` call."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        self.calls.append((sql, tuple(params)))
        return self._real.execute(sql, params)


class _CallOnlyRecorder:
    """Records ``execute`` calls WITHOUT a real backing connection."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.calls.append((sql, tuple(params)))


class _CapturingWriter:
    """Fake DBWriter double — captures the submitted job instead of running it."""

    def __init__(self) -> None:
        self.job: Any = None
        self.label: str = ""

    def submit(self, fn: Any, *, durable: bool = False, label: str = "") -> None:
        self.job = fn
        self.label = label
        return None


def test_submitted_job_issues_identical_sql_and_params_as_direct_write(
    tmp_path: Path,
) -> None:
    golden_conn = _mkconn(tmp_path, "golden.sqlite")
    golden_rec = _RecordingConn(golden_conn)
    log_tsmom_literature_shadow(
        cast(sqlite3.Connection, golden_rec), run_id="tick-7", signal_id=None,
        venue="okx", symbol="BTC-USDT", regime="trend",
        bars=_bars_from_closes(_CLOSES), now_ts=1_780_000_000,
    )
    golden_conn.close()
    assert len(golden_rec.calls) == 1
    golden_sql, golden_params = golden_rec.calls[0]
    assert "INSERT INTO gate_shadow_events" in golden_sql

    dbw_conn = _mkconn(tmp_path, "dbw.sqlite")
    writer = _CapturingWriter()
    log_tsmom_literature_shadow(
        dbw_conn, run_id="tick-7", signal_id=None,
        venue="okx", symbol="BTC-USDT", regime="trend",
        bars=_bars_from_closes(_CLOSES), now_ts=1_780_000_000,
        db_writer=cast(DBWriter, writer),
    )
    dbw_conn.close()

    assert writer.label == "tsmom_literature_shadow"
    assert writer.job is not None
    job_rec = _CallOnlyRecorder()
    writer.job(job_rec)
    assert len(job_rec.calls) == 1
    job_sql, job_params = job_rec.calls[0]
    assert job_sql == golden_sql
    # event_id (index 0) is a fresh uuid4 per call — every OTHER positional
    # param (run_id, signal_id, gate_id, venue, symbol, regime, technical_*,
    # gpt_decision, mismatch, cell_warm, created_ts) must match exactly.
    assert job_params[1:] == golden_params[1:]
    assert job_params[0] != golden_params[0]  # distinct event_id per call


async def test_db_writer_end_to_end_lands_a_row(tmp_path: Path) -> None:
    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        log_tsmom_literature_shadow(
            conn, run_id="tick-9", signal_id=None,
            venue="okx", symbol="BTC-USDT", regime="trend",
            bars=_bars_from_closes(_CLOSES), now_ts=1_780_000_000,
            db_writer=dbw,
        )
    finally:
        dbw.stop()  # final drain — the queued INSERT lands before this returns
    row = conn.execute(
        "SELECT gate_id, venue, symbol, regime, technical_decision, run_id, "
        "created_ts FROM gate_shadow_events"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[1:] == ("okx", "BTC-USDT", "trend", "long", "tick-9", 1_780_000_000)


def test_kill_switch_falls_back_to_direct_write(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        # db_writer is supplied but the kill switch disables routing — the
        # INSERT runs synchronously on ``conn`` (no drain wait needed).
        log_tsmom_literature_shadow(
            conn, run_id="tick-10", signal_id=None,
            venue="okx", symbol="BTC-USDT", regime="trend",
            bars=_bars_from_closes(_CLOSES), now_ts=1_780_000_000,
            db_writer=dbw,
        )
        count = conn.execute("SELECT COUNT(*) FROM gate_shadow_events").fetchone()[0]
    finally:
        dbw.stop()
    conn.close()
    assert count == 1
