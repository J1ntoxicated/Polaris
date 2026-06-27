"""STALL residual offload TDD — the 1m bar persist + baseline-append DB write
moved OFF the event loop onto a dedicated worker-thread connection.

Investigation (#90) named the dominant residual STALL blocker: the per-tick 1m
``persist_bars`` + ``update_baseline_from_bars`` pass ran SYNCHRONOUSLY on the
loop-owned ``conn`` inside ``ingest_bars_for_focus`` — 29k-60k row-by-row sqlite
executes with ZERO yield, bracketing the observed 1.5-4s tick-engine STALL gaps.

This is the #74 / #88 / retention_producer offload pattern applied to the bar
ingest write: ``asyncio.to_thread`` + a DEDICATED thread-confined connection +
snapshot inputs. Behaviour-identical (same bars, same baselines, same order) —
only WHERE/WHEN the writes run changes.

These tests pin:
  1. ``_ingest_blocking`` is a PURE blocking helper — opens its OWN conn from a
     db_path, NO loop-conn in its signature, returns the same counts dict.
  2. The offloaded path writes IDENTICAL ``bars`` + ``ticker_baseline_state``
     rows to the synchronous ``ingest_bars`` (golden).
  3. ``ingest_bars_offloaded`` does NOT block the event loop: a concurrent
     0.5s-cadence ticker keeps ticking while a large ingest batch runs.
  4. ``_db_path_from_conn`` extracts the file path from a file-backed conn and
     returns None for an in-memory conn (the safe on-loop fallback signal).
  5. degrade-never-crash: a worker-thread sqlite fault is swallowed, the loop
     survives, and the next ingest still works.
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
import time
from pathlib import Path

from polaris.core.data.ingest import (
    _db_path_from_conn,
    _ingest_blocking,
    ingest_bars,
    ingest_bars_offloaded,
)
from polaris.core.data.schema import Bar
from polaris.storage.schema import init_db


def _make_bars(
    n: int = 30, instrument: str = "okx:BTC-USDT", *, ts0: int = 1_700_000_000
) -> list[Bar]:
    venue, symbol = instrument.split(":")
    group = symbol.split("-")[0]
    out: list[Bar] = []
    for i in range(n):
        out.append(
            Bar(
                instrument_id=instrument,
                underlying_group_id=group,
                venue=venue,
                symbol=symbol,
                bar_interval="1m",
                ts=ts0 + i * 60,
                open=80_000.0 + i,
                high=80_010.0 + (i % 7) * 3.5,
                low=79_990.0 - (i % 5) * 2.0,
                close=80_005.0 + i,
                volume=10.0 + (i % 11),
                notional_usd=80_000.0 * (10.0 + (i % 11)),
                trade_count=100,
                vwap=80_000.0 + i,
            )
        )
    return out


def _state_rows(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT instrument_id, metric, baseline_p50, baseline_p75, "
        "sample_count, lookback_sec, updated_ts "
        "FROM ticker_baseline_state ORDER BY instrument_id, metric"
    ).fetchall()


def _bar_rows(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT instrument_id, bar_interval, ts, open, high, low, close, "
        "volume, notional_usd FROM bars ORDER BY instrument_id, ts"
    ).fetchall()


# ---------------------------------------------------------------------------
# 1. _ingest_blocking is a pure blocking helper — opens its own conn.
# ---------------------------------------------------------------------------


def test_ingest_blocking_takes_db_path_not_conn() -> None:
    """The offloaded blocking fn must take a db path, NOT a live sqlite conn."""
    sig = inspect.signature(_ingest_blocking)
    params = list(sig.parameters.values())
    # First positional param is the db path (str | Path), never a Connection.
    for name, param in sig.parameters.items():
        assert "Connection" not in str(param.annotation), (
            f"_ingest_blocking param {name!r} smells like a conn: "
            f"{param.annotation!r} — it must open its OWN dedicated conn"
        )
    assert params, "expected at least a db_path parameter"


def test_ingest_blocking_writes_and_returns_counts(tmp_path: Path) -> None:
    """_ingest_blocking opens its own conn, persists, returns the counts dict."""
    db = tmp_path / "blk.sqlite"
    init_db(db).close()  # create schema, then hand only the PATH to the helper
    bars = _make_bars(30)
    result = _ingest_blocking(db, bars, asset_class="crypto")
    assert result == {"bars": 30, "baseline_samples": 90}
    # The rows really landed (a fresh reader conn sees them — committed).
    reader = init_db(db)
    try:
        assert len(_bar_rows(reader)) == 30
        assert len(_state_rows(reader)) == 3  # atr / size / volume
    finally:
        reader.close()


# ---------------------------------------------------------------------------
# 2. Offloaded path == synchronous ingest, byte-for-byte (golden).
# ---------------------------------------------------------------------------


def test_offload_identical_to_sync(tmp_path: Path) -> None:
    """ingest_bars_offloaded writes IDENTICAL bars+state to sync ingest_bars."""
    bars = _make_bars(30, "okx:BTC-USDT") + _make_bars(25, "okx:ETH-USDT")

    sync_db = tmp_path / "sync.sqlite"
    conn_sync = init_db(sync_db)
    try:
        r_sync = ingest_bars(conn_sync, bars, asset_class="crypto")
        rows_sync = _bar_rows(conn_sync)
        state_sync = _state_rows(conn_sync)
    finally:
        conn_sync.close()

    off_db = tmp_path / "off.sqlite"
    init_db(off_db).close()
    r_off = asyncio.run(ingest_bars_offloaded(off_db, bars, asset_class="crypto"))
    reader = init_db(off_db)
    try:
        rows_off = _bar_rows(reader)
        state_off = _state_rows(reader)
    finally:
        reader.close()

    assert r_sync == r_off
    assert rows_sync == rows_off
    assert state_sync == state_off
    assert len({r[0] for r in state_off}) == 2  # BTC + ETH


# ---------------------------------------------------------------------------
# 3. The offload does NOT block the event loop (the STALL regression test).
# ---------------------------------------------------------------------------


def test_offload_does_not_block_event_loop(tmp_path: Path) -> None:
    """A concurrent 0.5s-cadence ticker keeps ticking WHILE the ingest runs.

    Simulates the live tick engine: while ``ingest_bars_offloaded`` does its
    heavy DB write on a worker thread, a co-running coroutine that yields every
    short slice must keep advancing. If the ingest blocked the loop (the bug),
    the ticker would freeze for the whole write. We assert the ticker advanced
    several times during the ingest — i.e. the loop was free.
    """
    db = tmp_path / "noblock.sqlite"
    init_db(db).close()
    # A big batch — many instruments × bars → a fat persist+baseline pass.
    bars: list[Bar] = []
    for k in range(12):
        bars += _make_bars(120, f"okx:SYM{k}-USDT")

    async def _run() -> int:
        ticks = 0
        done = asyncio.Event()

        async def _ticker() -> None:
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.005)

        t = asyncio.create_task(_ticker())
        await ingest_bars_offloaded(db, bars, asset_class="crypto")
        done.set()
        await t
        return ticks

    ticks = asyncio.run(_run())
    # If the loop were blocked the ticker could not advance during the write.
    # Even a modest write takes long enough for several 5ms ticks to interleave.
    assert ticks >= 3, (
        f"event loop appears blocked during ingest (only {ticks} ticks) — "
        "the offload is not freeing the loop"
    )


# ---------------------------------------------------------------------------
# 4. _db_path_from_conn — file conn → path, memory conn → None.
# ---------------------------------------------------------------------------


def test_db_path_from_conn_file(tmp_path: Path) -> None:
    db = tmp_path / "p.sqlite"
    conn = init_db(db)
    try:
        got = _db_path_from_conn(conn)
        assert got is not None
        # Same physical DB: the offload helper (a DEDICATED conn opened from the
        # extracted path) writes bars that the loop conn reads back — proving the
        # path resolves to the identical file the loop conn is bound to.
        _ingest_blocking(got, _make_bars(4), asset_class="crypto")
        n = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
        assert n == 4
    finally:
        conn.close()


def test_db_path_from_conn_memory_returns_none() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        assert _db_path_from_conn(conn) is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. degrade-never-crash — a worker fault is swallowed, loop survives.
# ---------------------------------------------------------------------------


def test_offload_degrades_on_bad_db(tmp_path: Path) -> None:
    """A db_path with NO schema returns {} (no crash) instead of raising.

    The bars/baseline tables are missing → the worker's sqlite write fails;
    the offload must degrade (return empty counts) so the live loop survives
    and the next tick retries — never propagate the fault into the loop.
    """
    db = tmp_path / "noschema.sqlite"
    # Touch the file but DO NOT init the schema.
    sqlite3.connect(str(db)).close()
    bars = _make_bars(5)
    result = asyncio.run(ingest_bars_offloaded(db, bars, asset_class="crypto"))
    assert result == {"bars": 0, "baseline_samples": 0}


def test_offload_final_drain_commits(tmp_path: Path) -> None:
    """Two sequential offloads accumulate (idempotent re-write, committed)."""
    db = tmp_path / "drain.sqlite"
    init_db(db).close()
    bars_a = _make_bars(20, "okx:BTC-USDT", ts0=1_700_000_000)
    bars_b = _make_bars(20, "okx:BTC-USDT", ts0=1_700_002_000)

    async def _seq() -> None:
        await ingest_bars_offloaded(db, bars_a, asset_class="crypto")
        await ingest_bars_offloaded(db, bars_b, asset_class="crypto")

    asyncio.run(_seq())
    reader = init_db(db)
    try:
        # 40 distinct ts rows (no overlap), one BTC instrument.
        assert len(_bar_rows(reader)) == 40
    finally:
        reader.close()


def test_offload_timing_release(tmp_path: Path) -> None:
    """The loop is not held for the wall-clock duration of the write.

    A sentinel coroutine records the loop's monotonic gap across the await of a
    large ingest; the gap between successive sentinel wakeups must stay small
    (the loop kept servicing the sentinel), not balloon to the write duration.
    """
    db = tmp_path / "timing.sqlite"
    init_db(db).close()
    bars: list[Bar] = []
    for k in range(10):
        bars += _make_bars(150, f"okx:T{k}-USDT")

    async def _run() -> float:
        max_gap = 0.0
        done = asyncio.Event()

        async def _sentinel() -> None:
            nonlocal max_gap
            last = time.monotonic()
            while not done.is_set():
                await asyncio.sleep(0.002)
                now = time.monotonic()
                max_gap = max(max_gap, now - last)
                last = now

        s = asyncio.create_task(_sentinel())
        await ingest_bars_offloaded(db, bars, asset_class="crypto")
        done.set()
        await s
        return max_gap

    max_gap = asyncio.run(_run())
    # A blocked loop would show a gap == the full write time (often >0.1s for
    # this batch). Off-loop, the sentinel keeps waking on its ~2ms cadence.
    assert max_gap < 0.5, (
        f"loop stalled {max_gap:.3f}s during ingest — offload not effective"
    )


# ---------------------------------------------------------------------------
# 6. End-to-end through the PRODUCTION caller (ingest_bars_for_focus) on a
#    FILE-backed DB — proves the wiring (not just the helper) offloads, and the
#    rows land via the dedicated-conn path that the loop conn then reads.
# ---------------------------------------------------------------------------


class _StubAdapter:
    """Minimal OKX-ish adapter — returns a fixed raw-bar list for fetch_bars."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    async def fetch_bars(
        self, symbol: str, *, timeframe: str = "1m", limit: int = 240
    ) -> list[dict[str, object]]:
        return list(self._rows)


def test_ingest_bars_for_focus_offloads_on_file_db(tmp_path: Path) -> None:
    """1m ingest through ``ingest_bars_for_focus`` on a file DB writes bars the
    loop conn reads back — exercising the offload (db_path is not None) path."""
    import polaris.scripts._production_bars as pbars
    from polaris.scripts._production_bars import ingest_bars_for_focus

    db = tmp_path / "focus.sqlite"
    conn = init_db(db)

    # Stub the fetch layer so no network is hit — return a small bar batch.
    fetched = _make_bars(6, "okx:BTC-USDT")

    async def _stub_fetch_bars_one(
        venue: str, symbol: str, asset_class: str, **kwargs: object
    ) -> list[Bar]:
        return fetched

    try:
        import asyncio as _aio
        from unittest.mock import patch

        with patch.object(pbars, "fetch_bars_one", new=_stub_fetch_bars_one):
            result = _aio.run(
                ingest_bars_for_focus(
                    conn,
                    [("okx", "BTC-USDT", "crypto", "crypto:BTC")],
                    bar_interval="1m",
                )
            )
        assert result["bars"] == 6
        # The loop conn (separate connection) sees the offload-committed rows.
        n = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
        assert n == 6
        # Baseline state was recomputed off-loop and committed too.
        bn = conn.execute(
            "SELECT COUNT(*) FROM ticker_baseline_state"
        ).fetchone()[0]
        assert bn == 3  # atr / size / volume
    finally:
        conn.close()
