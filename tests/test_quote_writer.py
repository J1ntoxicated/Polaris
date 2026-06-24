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


# ---------------------------------------------------------------------------
# tick_inflow — per-venue rolling buckets UPSERTed in the SAME flush txn.
# Design SSOT: vault/50_research/debates/tick_stream_decouple_2026-06-24.md.
# ---------------------------------------------------------------------------


def _qt_v(
    inst: str, venue: str, *, ts: int = NOW, mid: float = 100.0,
    bid_size: float = 1.0, ask_size: float = 1.0,
) -> QuoteTick:
    return QuoteTick(
        instrument_id=inst,
        venue=venue,
        symbol=inst.split(":", 1)[-1],
        ts=ts,
        bid=mid - 0.5,
        ask=mid + 0.5,
        mid=mid,
        spread_bps=10.0,
        bid_size=bid_size,
        ask_size=ask_size,
        source=f"{venue}_ws",
    )


def _read_inflow(db) -> dict[str, tuple[int, int, float, int]]:
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT venue, last_tick_ts, ticks_600s, max_flow_size_600s, "
        "window_started_at FROM tick_inflow"
    ).fetchall()
    conn.close()
    return {r[0]: (int(r[1]), int(r[2]), float(r[3]), int(r[4])) for r in rows}


async def test_flush_upserts_tick_inflow_per_venue(tmp_path) -> None:
    db = tmp_path / "q.sqlite"
    init_db(db).close()
    w = QuoteTickWriter(db)
    loop = asyncio.get_running_loop()
    # okx: 3 ticks, max flow size 6.0; capital: 1 tick, flow size 2.0.
    w.on_quote(_qt_v("okx:BTC-USDT", "okx", ts=NOW, bid_size=1.0, ask_size=1.0))
    w.on_quote(_qt_v("okx:ETH-USDT", "okx", ts=NOW + 1, bid_size=2.0, ask_size=4.0))
    w.on_quote(_qt_v("okx:SOL-USDT", "okx", ts=NOW + 2, bid_size=1.0, ask_size=1.0))
    w.on_quote(_qt_v("capital:US100", "capital", ts=NOW, bid_size=1.0, ask_size=1.0))
    await w._flush_once(loop)
    w.close()

    inflow = _read_inflow(db)
    assert inflow["okx"][0] == NOW + 2            # last_tick_ts = max ts
    assert inflow["okx"][1] == 3                  # ticks_600s
    assert inflow["okx"][2] == 6.0                # max(bid_size+ask_size)
    assert inflow["capital"][1] == 1
    assert inflow["capital"][2] == 2.0


async def test_tick_inflow_in_same_transaction_as_quotes(tmp_path) -> None:
    """quote rows + tick_inflow UPSERT must share ONE BEGIN..COMMIT (skew 0)."""
    db = tmp_path / "q.sqlite"
    init_db(db).close()
    w = QuoteTickWriter(db)
    w.on_quote(_qt_v("okx:BTC-USDT", "okx", ts=NOW))

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
    assert begins == 1 and commits == 1  # quotes + inflow in the one txn
    conn.set_trace_callback(None)
    w.close()


async def test_tick_inflow_window_started_at_is_first_tick_and_stable(tmp_path) -> None:
    """window_started_at freezes at the first tick ts per venue (restart marker)."""
    db = tmp_path / "q.sqlite"
    init_db(db).close()
    w = QuoteTickWriter(db)
    loop = asyncio.get_running_loop()
    w.on_quote(_qt_v("okx:BTC-USDT", "okx", ts=NOW))
    await w._flush_once(loop)
    w.on_quote(_qt_v("okx:BTC-USDT", "okx", ts=NOW + 30))
    await w._flush_once(loop)
    w.close()
    inflow = _read_inflow(db)
    assert inflow["okx"][3] == NOW            # window_started_at unchanged
    assert inflow["okx"][0] == NOW + 30       # last_tick_ts advanced


async def test_tick_inflow_buckets_drop_outside_600s(tmp_path) -> None:
    """A tick older than 600s before the latest must not count in ticks_600s."""
    db = tmp_path / "q.sqlite"
    init_db(db).close()
    w = QuoteTickWriter(db)
    loop = asyncio.get_running_loop()
    w.on_quote(_qt_v("okx:BTC-USDT", "okx", ts=NOW))
    w.on_quote(_qt_v("okx:ETH-USDT", "okx", ts=NOW + 700))  # 700s later → old drops
    await w._flush_once(loop)
    w.close()
    inflow = _read_inflow(db)
    assert inflow["okx"][1] == 1              # only the recent tick in 600s window
    assert inflow["okx"][0] == NOW + 700
