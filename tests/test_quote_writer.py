"""Unit tests for QuoteTickWriter (P4 WS foundation, M1/M2).

Covers:
- coalesce: last-write-wins per instrument_id in the in-mem buffer.
- on_quote updates live_px with a monotonic stamp, no DB hit.
- flush: snapshot-then-clear has no race; one BEGIN..COMMIT per batch (verified
  via a sqlite trace that counts COMMIT statements).
- INSERT OR REPLACE collapses duplicate (instrument_id, ts) PKs.
- final drain on stop persists the tail.
"""

from __future__ import annotations

import asyncio
import sqlite3

from polaris.core.data.quote_writer import QuoteTickWriter
from polaris.core.data.schema import QuoteTick
from polaris.storage.schema import init_db

NOW = 1_900_000_000


def _qt(inst: str, *, ts: int = NOW, mid: float = 100.0) -> QuoteTick:
    return QuoteTick(
        instrument_id=inst,
        venue="okx",
        symbol=inst.split(":", 1)[-1],
        ts=ts,
        bid=mid - 0.5,
        ask=mid + 0.5,
        mid=mid,
        spread_bps=10.0,
        source="okx_ws",
    )


def test_on_quote_coalesces_last_write_wins() -> None:
    w = QuoteTickWriter(":memory:")
    w.on_quote(_qt("okx:BTC-USDT", mid=100.0))
    w.on_quote(_qt("okx:BTC-USDT", mid=101.0))
    w.on_quote(_qt("okx:ETH-USDT", mid=50.0))
    assert len(w._buf) == 2
    assert w._buf["okx:BTC-USDT"].mid == 101.0


def test_on_quote_updates_live_px_monotonic() -> None:
    w = QuoteTickWriter(":memory:")
    assert w.live_px("okx:BTC-USDT") is None
    w.on_quote(_qt("okx:BTC-USDT", mid=100.0))
    px = w.live_px("okx:BTC-USDT")
    assert px is not None
    mid, mono = px
    assert mid == 100.0
    assert mono > 0.0


async def test_flush_writes_rows_and_clears_buffer(tmp_path) -> None:
    db = tmp_path / "q.sqlite"
    init_db(db).close()
    w = QuoteTickWriter(db)
    w.on_quote(_qt("okx:BTC-USDT", mid=100.0))
    w.on_quote(_qt("okx:ETH-USDT", mid=50.0))

    loop = asyncio.get_running_loop()
    await w._flush_once(loop)

    assert w._buf == {}  # snapshot-then-clear emptied the buffer
    assert w.rows_written == 2
    w.close()

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT instrument_id, mid FROM quote_ticks ORDER BY instrument_id"
    ).fetchall()
    conn.close()
    assert rows == [("okx:BTC-USDT", 100.0), ("okx:ETH-USDT", 50.0)]


async def test_flush_is_single_transaction(tmp_path) -> None:
    """A batch must produce exactly ONE COMMIT (M1a: not one-per-row)."""
    db = tmp_path / "q.sqlite"
    init_db(db).close()
    w = QuoteTickWriter(db)
    for i in range(10):
        w.on_quote(_qt(f"okx:SYM{i}-USDT", mid=float(i + 1)))

    commits = 0
    begins = 0

    def _trace(stmt: str) -> None:
        nonlocal commits, begins
        s = stmt.strip().upper()
        if s.startswith("COMMIT"):
            commits += 1
        elif s.startswith("BEGIN"):
            begins += 1

    # Ensure the dedicated conn exists, then attach the trace.
    conn = w._ensure_conn()
    conn.set_trace_callback(_trace)

    loop = asyncio.get_running_loop()
    await w._flush_once(loop)

    assert begins == 1
    assert commits == 1
    assert w.rows_written == 10
    conn.set_trace_callback(None)
    w.close()


async def test_insert_or_replace_dedups_same_pk(tmp_path) -> None:
    """Same (instrument_id, ts) across two flushes → one row, latest wins."""
    db = tmp_path / "q.sqlite"
    init_db(db).close()
    w = QuoteTickWriter(db)
    loop = asyncio.get_running_loop()

    w.on_quote(_qt("okx:BTC-USDT", ts=NOW, mid=100.0))
    await w._flush_once(loop)
    w.on_quote(_qt("okx:BTC-USDT", ts=NOW, mid=200.0))
    await w._flush_once(loop)
    w.close()

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT mid FROM quote_ticks WHERE instrument_id='okx:BTC-USDT'"
    ).fetchall()
    conn.close()
    assert rows == [(200.0,)]


async def test_run_flush_loop_final_drain_on_stop(tmp_path) -> None:
    db = tmp_path / "q.sqlite"
    init_db(db).close()
    w = QuoteTickWriter(db, flush_interval_sec=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(w.run_flush_loop(stop))

    w.on_quote(_qt("okx:BTC-USDT", mid=100.0))
    # Stop immediately; the final drain must still persist the buffered tick.
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    w.close()

    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM quote_ticks").fetchone()[0]
    conn.close()
    assert n == 1


async def test_flush_empty_buffer_is_noop(tmp_path) -> None:
    db = tmp_path / "q.sqlite"
    init_db(db).close()
    w = QuoteTickWriter(db)
    loop = asyncio.get_running_loop()
    await w._flush_once(loop)  # nothing buffered
    assert w.flush_count == 0
    assert w.rows_written == 0
    w.close()
