"""ingest offload routed through the shared DBWriter (db-writer-reader-split,
design SSOT: vault/50_research/db-writer-reader-split-design_2026-07-08.md).

DEMO/PAPER only. temp DB only. Default (``db_writer=None``) stays byte-identical
to the pre-split dedicated-conn offload path — covered by
``test_ingest_offload.py``. This file covers ONLY the new opt-in path.
"""

from __future__ import annotations

from polaris.core.data.ingest import (
    ingest_bars,
    ingest_bars_offloaded,
    persist_bars_offloaded,
)
from polaris.core.data.schema import Bar
from polaris.storage.db_writer import DBWriter
from polaris.storage.schema import connect, init_db


def _make_bars(n: int = 5, instrument: str = "okx:BTC-USDT") -> list[Bar]:
    venue, symbol = instrument.split(":")
    group = symbol.split("-")[0]
    return [
        Bar(
            instrument_id=instrument, underlying_group_id=group, venue=venue,
            symbol=symbol, bar_interval="1m", ts=1_700_000_000 + i * 60,
            open=80_000.0 + i, high=80_010.0, low=79_990.0, close=80_005.0 + i,
            volume=10.0, notional_usd=800_000.0, trade_count=100, vwap=80_000.0,
        )
        for i in range(n)
    ]


async def test_ingest_bars_offloaded_via_db_writer_matches_golden(tmp_path) -> None:
    bars = _make_bars()
    golden_db = tmp_path / "golden.sqlite"
    golden_conn = init_db(golden_db)
    golden = ingest_bars(golden_conn, bars, asset_class="crypto")
    golden_conn.close()

    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    try:
        result = await ingest_bars_offloaded(
            db, bars, asset_class="crypto", db_writer=dbw
        )
    finally:
        dbw.stop()
    assert result == golden
    conn = connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    conn.close()
    assert rows == len(bars)


async def test_persist_bars_offloaded_via_db_writer(tmp_path) -> None:
    bars = _make_bars(n=3)
    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    try:
        n = await persist_bars_offloaded(db, bars, db_writer=dbw)
    finally:
        dbw.stop()
    assert n == len(bars)
    conn = connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    conn.close()
    assert rows == len(bars)


async def test_kill_switch_falls_back_to_dedicated_conn(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_DBWRITER_ENABLED", "0")
    bars = _make_bars(n=2)
    db = tmp_path / "dbw.sqlite"
    init_db(db).close()
    dbw = DBWriter(db, batch_max=8, drain_ms=10)
    dbw.start()
    try:
        # db_writer is supplied but the kill switch disables routing — falls
        # back to the pre-split asyncio.to_thread + dedicated-conn path, which
        # still succeeds (it just doesn't use ``dbw`` at all).
        result = await ingest_bars_offloaded(db, bars, db_writer=dbw)
    finally:
        dbw.stop()
    assert result["bars"] == len(bars)
