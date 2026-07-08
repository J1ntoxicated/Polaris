"""TechnicalStoreWriter routed through the shared DBWriter (db-writer-reader-
split, design SSOT: vault/50_research/db-writer-reader-split-design_2026-07-08.md).

DEMO/PAPER only. temp DB only. Default (``db_writer=None``) stays byte-identical
to the pre-split dedicated-conn path — covered by ``test_technical_store_writer.py``.
This file covers ONLY the new opt-in path.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from polaris.core.data.technical_store_writer import TechnicalStoreWriter
from polaris.storage.db_writer import DBWriter
from polaris.storage.schema import connect_ro, init_db

INST = "okx:BTC-USDT"
TF = "1H"


async def test_flush_via_db_writer_persists_rows_no_dedicated_conn(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    try:
        w = TechnicalStoreWriter(db, db_writer=dbw)
        w.record(
            instrument_id=INST, bar_interval=TF,
            values={"rsi_14": 58.0}, computed_ts=2000, source_bar_ts=1900,
        )
        await w._flush_once(asyncio.get_running_loop())
        assert w._conn is None
        for _ in range(50):
            if dbw.jobs_processed >= 1:
                break
            time.sleep(0.02)
    finally:
        dbw.stop()
    ro = connect_ro(db)
    row = ro.execute(
        "SELECT value FROM ticker_technicals WHERE instrument_id = ? "
        "AND bar_interval = ? AND indicator = 'rsi_14'",
        (INST, TF),
    ).fetchone()
    ro.close()
    assert row is not None
    assert row[0] == 58.0


async def test_kill_switch_falls_back_to_dedicated_conn(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    db = tmp_path / "t.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    try:
        w = TechnicalStoreWriter(db, db_writer=dbw)
        w.record(
            instrument_id=INST, bar_interval=TF,
            values={"rsi_14": 61.0}, computed_ts=2100, source_bar_ts=2050,
        )
        await w._flush_once(asyncio.get_running_loop())
        assert w._conn is not None
        w.close()
    finally:
        dbw.stop()
