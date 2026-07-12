"""regime_v2 shadow UPDATE routed through the shared DBWriter
(db-writer-reader-split roadmap 1.10, 2026-07-12 audit item [A] —
``_safe_record_regime_v2_shadow`` inside the ``regime_compute`` tick stage).

DEMO/PAPER only. temp DB only. Default (``db_writer=None``) stays
byte-identical to the pre-migration direct-write path — covered by
``test_regime_v2_shadow.py``. This file covers ONLY the new opt-in path:
(1) the submitted job issues the IDENTICAL SQL/params as the direct-write
branch, (2) an end-to-end real-DBWriter run lands the same row a direct
write would, (3) the kill switch falls back to the direct-conn path.
"""

from __future__ import annotations

import sqlite3
from typing import Any, cast

import polaris.scripts._production_layers as layers_mod
from polaris.scripts._production_layers import compute_and_flip_regime
from polaris.storage.db_writer import DBWriter
from polaris.storage.schema import connect, init_db

NOW = 1_780_000_000


def _run(
    conn: sqlite3.Connection, *, db_writer: DBWriter | None = None
) -> str:
    return compute_and_flip_regime(
        conn, venue="okx", underlying_group_id="crypto:BTC", bars=[],
        now_ts=NOW, db_writer=db_writer,
    )


class _RecordingConn:
    """Wraps a real sqlite3.Connection and logs every ``execute`` call —
    lets a test assert the exact SQL text + params a code path issued."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        self.calls.append((sql, tuple(params)))
        return self._real.execute(sql, params)


class _CallOnlyRecorder:
    """Records ``execute`` calls WITHOUT a real backing connection — the
    submitted job's whole contract is the (sql, params) tuple it issues, not
    the row a schemaless stub would fail to write."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.calls.append((sql, tuple(params)))


class _CapturingWriter:
    """Fake DBWriter double — captures the submitted job instead of running
    it, so the test can execute it later against a controlled connection."""

    def __init__(self) -> None:
        self.job: Any = None
        self.label: str = ""

    def submit(self, fn: Any, *, durable: bool = False, label: str = "") -> None:
        self.job = fn
        self.label = label
        return None


def test_submitted_job_issues_identical_sql_and_params_as_direct_write(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    monkeypatch.setattr(layers_mod, "classify_regime_v2", lambda _bars: "up_normal")

    golden_db = tmp_path / "golden.sqlite"
    golden_conn = init_db(golden_db)
    golden_rec = _RecordingConn(golden_conn)
    _run(cast(sqlite3.Connection, golden_rec))  # duck-typed: only .execute used
    golden_conn.close()
    v2_calls = [c for c in golden_rec.calls if "regime_v2" in c[0]]
    assert len(v2_calls) == 1
    golden_sql, golden_params = v2_calls[0]
    assert "UPDATE regime_state SET regime_v2" in golden_sql

    dbw_db = tmp_path / "dbw.sqlite"
    dbw_conn = init_db(dbw_db)
    writer = _CapturingWriter()
    _run(dbw_conn, db_writer=cast(DBWriter, writer))
    dbw_conn.close()

    assert writer.label == "regime_v2_shadow"
    assert writer.job is not None
    job_rec = _CallOnlyRecorder()
    writer.job(job_rec)
    assert job_rec.calls[0] == (golden_sql, golden_params)


async def test_db_writer_end_to_end_lands_same_row_as_direct_write(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    monkeypatch.setattr(layers_mod, "classify_regime_v2", lambda _bars: "down_expansion")

    golden_db = tmp_path / "golden.sqlite"
    golden_conn = init_db(golden_db)
    _run(golden_conn)
    golden_row = golden_conn.execute(
        "SELECT regime, regime_v2 FROM regime_state "
        "WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()
    golden_conn.close()

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        _run(conn, db_writer=dbw)
    finally:
        dbw.stop()  # final drain — the queued UPDATE lands before this returns
    row = conn.execute(
        "SELECT regime, regime_v2 FROM regime_state "
        "WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
    ).fetchone()
    conn.close()
    assert tuple(row) == tuple(golden_row)


def test_kill_switch_falls_back_to_direct_write(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    monkeypatch.setattr(layers_mod, "classify_regime_v2", lambda _bars: "flat_normal")

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    conn = connect(db)
    try:
        # db_writer is supplied but the kill switch disables routing — the
        # UPDATE runs synchronously on ``conn`` (no drain wait needed).
        _run(conn, db_writer=dbw)
        row = conn.execute(
            "SELECT regime_v2 FROM regime_state "
            "WHERE venue='okx' AND underlying_group_id='crypto:BTC'"
        ).fetchone()
    finally:
        dbw.stop()
    conn.close()
    assert row[0] == "flat_normal"
