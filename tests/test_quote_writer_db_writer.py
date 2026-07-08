"""QuoteTickWriter routed through the shared DBWriter (db-writer-reader-split,
design SSOT: vault/50_research/db-writer-reader-split-design_2026-07-08.md).

DEMO/PAPER only. temp DB only. Default (``db_writer=None``) stays byte-identical
to the pre-split dedicated-conn path — covered by ``test_quote_writer.py``. This
file covers ONLY the new opt-in path: rows land via the shared writer, the
kill switch (``POLARIS_DBWRITER_ENABLED=0``) reverts to the dedicated conn even
when a ``db_writer`` is supplied, and no dedicated conn is opened when routed.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from polaris.core.data.quote_writer import QuoteTickWriter
from polaris.core.data.schema import QuoteTick
from polaris.storage.db_writer import DBWriter
from polaris.storage.schema import connect_ro, init_db

NOW = 1_900_000_000


def _qt(inst: str, *, mid: float = 100.0) -> QuoteTick:
    return QuoteTick(
        instrument_id=inst, venue="okx", symbol=inst.split(":", 1)[-1],
        ts=NOW, bid=mid - 0.5, ask=mid + 0.5, mid=mid, spread_bps=10.0,
        source="okx_ws",
    )


async def test_flush_via_db_writer_persists_rows_no_dedicated_conn(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    try:
        w = QuoteTickWriter(db, db_writer=dbw)
        w.on_quote(_qt("okx:BTC-USDT"))
        w.on_quote(_qt("okx:ETH-USDT", mid=50.0))
        await w._flush_once(asyncio.get_running_loop())
        # No executor-offloaded dedicated conn was ever opened for this writer.
        assert w._conn is None
        for _ in range(50):
            if dbw.jobs_processed >= 1:
                break
            time.sleep(0.02)
    finally:
        dbw.stop()
    ro = connect_ro(db)
    count = ro.execute("SELECT COUNT(*) FROM quote_ticks").fetchone()[0]
    ro.close()
    assert count == 2


async def test_kill_switch_falls_back_to_dedicated_conn(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    db = tmp_path / "t.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    try:
        w = QuoteTickWriter(db, db_writer=dbw)
        w.on_quote(_qt("okx:BTC-USDT"))
        await w._flush_once(asyncio.get_running_loop())
        # Kill switch off -> legacy dedicated-conn path used despite db_writer
        # being supplied -> its own conn got opened.
        assert w._conn is not None
        w.close()
    finally:
        dbw.stop()
    ro = connect_ro(db)
    count = ro.execute("SELECT COUNT(*) FROM quote_ticks").fetchone()[0]
    ro.close()
    assert count == 1
