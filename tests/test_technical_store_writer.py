"""STALL fix #88 — TechnicalStoreWriter offloads the technical-store write off
the shared tick conn (mirrors QuoteTickWriter, the #74-reviewed STALL-safe shape).

Root cause (#74 regression): the ④ #12 technical store wrote SYNCHRONOUSLY on the
loop thread via the shared tick connection (``upsert_technicals(conn, ...)`` inline
in the focus fan-out). That heavy per-(venue,symbol,timeframe) writer contended the
WAL single-writer lock with the 1Hz quote_writer flush → SQLITE_BUSY → busy_timeout
stalls the tick cadence. This writer decouples it: ``record`` is pure in-mem on the
loop thread (no DB hit), and a 1Hz flush offloads the executemany to the default
executor on a DEDICATED connection — the loop thread never touches SQLite for it.

Covers:
- record: in-mem coalesce (last-write-wins per instrument+interval), ZERO DB writes.
- flush: snapshot-then-clear → rows persisted via the dedicated conn; buffer emptied.
- single BEGIN..COMMIT per batch (autocommit conn would commit per executemany).
- rows continue to be written (judge evidence preserved — flow_not_block).
- final drain on stop persists the tail.
- missing-table write degrades (no crash; the flush loop survives).
"""

from __future__ import annotations

import asyncio
import sqlite3

from polaris.core.data.technical_store import read_technicals
from polaris.core.data.technical_store_writer import TechnicalStoreWriter
from polaris.storage.schema import init_db

INST = "okx:BTC-USDT"
TF = "1H"


def test_record_coalesces_last_write_wins_no_db() -> None:
    """record() is pure in-mem: LWW per (instrument, interval), no DB hit."""
    w = TechnicalStoreWriter(":memory:")
    w.record(
        instrument_id=INST, bar_interval=TF,
        values={"rsi_14": 58.0, "adx_14": 27.0},
        computed_ts=2000, source_bar_ts=1900,
    )
    w.record(
        instrument_id=INST, bar_interval=TF,
        values={"rsi_14": 61.0, "adx_14": 30.0},
        computed_ts=2100, source_bar_ts=2050,
    )
    # Coalesce: one entry per (instrument, interval), latest snapshot wins.
    assert len(w._buf) == 1
    entry = w._buf[(INST, TF)]
    assert entry.values["rsi_14"] == 61.0
    assert entry.computed_ts == 2100
    # No dedicated conn was ever opened by record() (zero DB writes on loop thread).
    assert w._conn is None


def test_record_empty_values_is_noop() -> None:
    """An empty indicator set buffers nothing (no NULL noise)."""
    w = TechnicalStoreWriter(":memory:")
    w.record(
        instrument_id=INST, bar_interval=TF, values={},
        computed_ts=2000, source_bar_ts=1900,
    )
    assert w._buf == {}


async def test_flush_writes_rows_and_clears_buffer(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    init_db(db).close()
    w = TechnicalStoreWriter(db)
    w.record(
        instrument_id=INST, bar_interval=TF,
        values={"rsi_14": 58.0, "adx_14": 27.0},
        computed_ts=2000, source_bar_ts=1900,
    )
    w.record(
        instrument_id="okx:ETH-USDT", bar_interval=TF,
        values={"rsi_14": 44.0},
        computed_ts=2000, source_bar_ts=1900,
    )

    loop = asyncio.get_running_loop()
    await w._flush_once(loop)

    assert w._buf == {}  # snapshot-then-clear emptied the buffer
    assert w.rows_written == 3  # 2 + 1 indicators
    w.close()

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT instrument_id, indicator, value FROM ticker_technicals "
        "ORDER BY instrument_id, indicator"
    ).fetchall()
    conn.close()
    assert rows == [
        ("okx:BTC-USDT", "adx_14", 27.0),
        ("okx:BTC-USDT", "rsi_14", 58.0),
        ("okx:ETH-USDT", "rsi_14", 44.0),
    ]


async def test_flush_is_single_transaction(tmp_path) -> None:
    """A batch must produce exactly ONE COMMIT (autocommit would be one-per-write)."""
    db = tmp_path / "t.sqlite"
    init_db(db).close()
    w = TechnicalStoreWriter(db)
    for i in range(8):
        w.record(
            instrument_id=f"okx:SYM{i}-USDT", bar_interval=TF,
            values={"rsi_14": float(i), "adx_14": float(i)},
            computed_ts=2000, source_bar_ts=1900,
        )

    commits = begins = 0

    def _trace(stmt: str) -> None:
        nonlocal commits, begins
        s = stmt.strip().upper()
        if s.startswith("COMMIT"):
            commits += 1
        elif s.startswith("BEGIN"):
            begins += 1

    conn = w._ensure_conn()
    conn.set_trace_callback(_trace)

    loop = asyncio.get_running_loop()
    await w._flush_once(loop)

    assert begins == 1
    assert commits == 1
    assert w.rows_written == 16  # 8 entries × 2 indicators
    conn.set_trace_callback(None)
    w.close()


async def test_flush_lww_overwrites_same_pk(tmp_path) -> None:
    """A re-record across two flushes overwrites the same row (judge sees latest)."""
    db = tmp_path / "t.sqlite"
    init_db(db).close()
    w = TechnicalStoreWriter(db)
    loop = asyncio.get_running_loop()

    w.record(
        instrument_id=INST, bar_interval=TF, values={"rsi_14": 58.0},
        computed_ts=2000, source_bar_ts=1900,
    )
    await w._flush_once(loop)
    w.record(
        instrument_id=INST, bar_interval=TF, values={"rsi_14": 70.0},
        computed_ts=2100, source_bar_ts=2050,
    )
    await w._flush_once(loop)
    w.close()

    conn = init_db(db)
    stored = read_technicals(conn, instrument_id=INST, bar_interval=TF)
    conn.close()
    assert stored["rsi_14"]["value"] == 70.0  # LWW, no append
    # Bounded: a single row for the (instrument, interval, indicator) PK.
    conn = sqlite3.connect(db)
    n = conn.execute(
        "SELECT COUNT(*) FROM ticker_technicals WHERE instrument_id=? AND indicator='rsi_14'",
        (INST,),
    ).fetchone()[0]
    conn.close()
    assert n == 1


async def test_run_flush_loop_final_drain_on_stop(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    init_db(db).close()
    w = TechnicalStoreWriter(db, flush_interval_sec=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(w.run_flush_loop(stop))

    w.record(
        instrument_id=INST, bar_interval=TF, values={"rsi_14": 58.0},
        computed_ts=2000, source_bar_ts=1900,
    )
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    w.close()

    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM ticker_technicals").fetchone()[0]
    conn.close()
    assert n == 1


async def test_flush_empty_buffer_is_noop(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    init_db(db).close()
    w = TechnicalStoreWriter(db)
    loop = asyncio.get_running_loop()
    await w._flush_once(loop)
    assert w.flush_count == 0
    assert w.rows_written == 0
    w.close()


async def test_flush_missing_table_degrades_loop_survives(tmp_path) -> None:
    """A flush against a DB with no technicals table never crashes the flush loop.

    degrade-never-crash: the missing-table error is logged + the batch dropped by
    ``_flush_once`` (it swallows the executor exception), so a missing migration
    can never kill the loop — exactly the quote_writer contract.
    """
    db = tmp_path / "bare.sqlite"
    sqlite3.connect(db).close()  # no init_db → ticker_technicals absent
    w = TechnicalStoreWriter(db)
    w.record(
        instrument_id=INST, bar_interval=TF, values={"rsi_14": 58.0},
        computed_ts=2000, source_bar_ts=1900,
    )
    loop = asyncio.get_running_loop()
    # Must NOT raise (the batch is dropped, the loop would carry on).
    await w._flush_once(loop)
    w.close()
