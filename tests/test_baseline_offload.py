"""Offload TDD — baseline pure-compute moved off the event loop.

The per-1m-tick baseline recompute re-reads the rolling sample window (DB,
loop-bound) then sorts + percentiles each list (PURE compute). To keep the
real-time tick engine responsive, the sort/percentile batch is offloaded to a
worker thread via ``asyncio.to_thread`` while ALL DB access stays on the loop.

These tests pin:
  1. ``compute_baselines_batch`` is a PURE function — plain data in, plain data
     out, NO ``sqlite3`` connection in its signature.
  2. The batch helper produces byte-identical output to calling the original
     per-item ``compute_baseline`` (golden).
  3. ``update_baseline_from_bars_async`` (loop→thread→loop) writes IDENTICAL
     ``ticker_baseline_state`` rows to the synchronous ``update_baseline_from_bars``.
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from pathlib import Path

from polaris.core.data.baseline import compute_baseline
from polaris.core.data.ingest import (
    compute_baselines_batch,
    update_baseline_from_bars,
    update_baseline_from_bars_async,
)
from polaris.core.data.schema import Bar
from polaris.storage.schema import init_db


def _make_bars(n: int = 30, instrument: str = "okx:BTC-USDT") -> list[Bar]:
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
                ts=1_700_000_000 + i * 60,
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


def test_compute_baselines_batch_is_pure_no_conn() -> None:
    """The offloaded fn must take plain data — no sqlite connection param."""
    sig = inspect.signature(compute_baselines_batch)
    for name, param in sig.parameters.items():
        ann = param.annotation
        assert "Connection" not in str(ann), (
            f"compute_baselines_batch param {name!r} smells like a conn: {ann!r}"
        )
    # And it must run with NO conn argument at all.
    items = [("okx:BTC-USDT", "BTC", "atr", [1.0, 3.0, 2.0], 1_700_000_000, 604800)]
    out = compute_baselines_batch(items)
    assert len(out) == 1
    iid, gid, bv = out[0]
    assert iid == "okx:BTC-USDT"
    assert gid == "BTC"
    assert bv.metric == "atr"


def test_compute_baselines_batch_matches_compute_baseline_golden() -> None:
    """Batch helper == per-item compute_baseline, value-for-value."""
    samples = [5.0, 1.0, 3.0, 2.0, 9.0, 4.0, 7.0]
    ts = 1_700_000_500
    lookback = 604800
    golden = compute_baseline(
        metric="size", samples=samples, updated_ts=ts, lookback_sec=lookback
    )
    out = compute_baselines_batch(
        [("okx:BTC-USDT", "BTC", "size", samples, ts, lookback)]
    )
    _, _, got = out[0]
    assert got == golden


def _state_rows(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT instrument_id, metric, baseline_p50, baseline_p75, "
        "sample_count, lookback_sec, updated_ts "
        "FROM ticker_baseline_state ORDER BY instrument_id, metric"
    ).fetchall()


def test_async_offload_identical_to_sync(tmp_path: Path) -> None:
    """loop→thread→loop async path writes IDENTICAL state to the sync path."""
    bars = _make_bars(30)

    sync_db = tmp_path / "sync.sqlite"
    conn_sync = init_db(sync_db)
    try:
        n_sync = update_baseline_from_bars(conn_sync, bars, asset_class="crypto")
        rows_sync = _state_rows(conn_sync)
    finally:
        conn_sync.close()

    async_db = tmp_path / "async.sqlite"
    conn_async = init_db(async_db)
    try:
        n_async = asyncio.run(
            update_baseline_from_bars_async(conn_async, bars, asset_class="crypto")
        )
        rows_async = _state_rows(conn_async)
    finally:
        conn_async.close()

    assert n_sync == n_async == 90  # 30 bars × 3 metrics
    assert rows_sync == rows_async
    assert len(rows_async) == 3  # atr / size / volume


def test_async_offload_multi_instrument(tmp_path: Path) -> None:
    """Multiple instruments in one batch still match the sync golden."""
    bars = _make_bars(20, "okx:BTC-USDT") + _make_bars(20, "okx:ETH-USDT")

    sync_db = tmp_path / "sync2.sqlite"
    conn_sync = init_db(sync_db)
    try:
        update_baseline_from_bars(conn_sync, bars, asset_class="crypto")
        rows_sync = _state_rows(conn_sync)
    finally:
        conn_sync.close()

    async_db = tmp_path / "async2.sqlite"
    conn_async = init_db(async_db)
    try:
        asyncio.run(
            update_baseline_from_bars_async(conn_async, bars, asset_class="crypto")
        )
        rows_async = _state_rows(conn_async)
    finally:
        conn_async.close()

    assert rows_sync == rows_async
    assert len({r[0] for r in rows_async}) == 2  # BTC + ETH
