"""Layer 1 — Bar/tick ingest (venue adapter → SQLite + baseline sample update).

Spec source:
- vault/30_components/layer-1-canonical-baseline.md (Q1 bars + Q2 baseline samples)

Pipeline:
    1. Fetch canonical Bar list (already cross-venue normalized via adapter).
    2. Persist to ``bars`` table (PRIMARY KEY (instrument_id, bar_interval, ts)).
    3. Append ``ticker_baseline_samples`` rows (atr / size / volume metrics) for
       each closed bar.
    4. Recompute ``ticker_baseline_state`` from rolling window after batch.

The baseline metrics derived per-bar:
- ``volume`` — bar.volume (raw)
- ``atr`` — bar.high - bar.low (per-bar true-range surrogate; HLC TR computed
  externally if previous-close available; per-bar fallback is fine for warmup)
- ``size`` — bar.notional_usd (USD-equivalent)
- ``signal`` and ``pnl_std`` are emitted by strategies/closes, not by this
  ingest path.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterable

from polaris.core.data.baseline import (
    _default_lookback,
    append_sample,
    compute_baseline,
    read_samples_window,
    update_baseline_from_window,
    upsert_baseline_state,
)
from polaris.core.data.schema import Bar, BaselineValue

logger = logging.getLogger(__name__)

__all__ = [
    "BAR_BASELINE_METRICS",
    "compute_baselines_batch",
    "ingest_bars",
    "ingest_bars_async",
    "persist_bars",
    "update_baseline_from_bars",
    "update_baseline_from_bars_async",
]

# Metrics derivable from a closed bar (Q2 spec).
BAR_BASELINE_METRICS: tuple[str, ...] = ("atr", "size", "volume")


def persist_bars(conn: sqlite3.Connection, bars: Iterable[Bar]) -> int:
    """Idempotent insert into the ``bars`` table.

    Returns the number of bars *processed* — duplicates upsert in place via
    ``INSERT OR REPLACE``, so the count is "rows seen" not
    "rows newly inserted" (codex Day 6 P2 contract clarification).
    """
    n = 0
    for b in bars:
        conn.execute(
            """
            INSERT OR REPLACE INTO bars
                (instrument_id, underlying_group_id, venue, symbol, bar_interval,
                 ts, open, high, low, close, volume, notional_usd, trade_count,
                 vwap, bid_close, ask_close, spread_bps_close, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                b.instrument_id,
                b.underlying_group_id,
                b.venue,
                b.symbol,
                b.bar_interval,
                int(b.ts),
                float(b.open),
                float(b.high),
                float(b.low),
                float(b.close),
                float(b.volume),
                float(b.notional_usd),
                int(b.trade_count),
                float(b.vwap),
                float(b.bid_close),
                float(b.ask_close),
                float(b.spread_bps_close),
                b.source,
            ),
        )
        n += 1
    return n


def update_baseline_from_bars(
    conn: sqlite3.Connection,
    bars: Iterable[Bar],
    *,
    asset_class: str = "",
) -> int:
    """Append per-bar samples to ``ticker_baseline_samples``.

    Each Bar produces 3 rows (atr / size / volume). Returns total rows
    appended.

    Day 1 P2 fix (codex Day 5 review): materialize ``bars`` once at entry
    so callers may pass any ``Iterable`` (generator, map, filter). The
    previous two-pass implementation silently produced 0 baseline-state
    upserts when a generator was supplied — the second loop saw an
    exhausted iterator and skipped recompute entirely.
    """
    bars_list: list[Bar] = list(bars)
    n = 0
    for b in bars_list:
        atr_value = max(0.0, float(b.high) - float(b.low))
        notional = float(b.notional_usd) if b.notional_usd > 0 else float(b.close) * float(b.volume)
        # atr
        append_sample(
            conn,
            instrument_id=b.instrument_id,
            underlying_group_id=b.underlying_group_id,
            metric="atr",
            ts=int(b.ts),
            value=atr_value,
        )
        # size
        append_sample(
            conn,
            instrument_id=b.instrument_id,
            underlying_group_id=b.underlying_group_id,
            metric="size",
            ts=int(b.ts),
            value=float(notional),
        )
        # volume
        append_sample(
            conn,
            instrument_id=b.instrument_id,
            underlying_group_id=b.underlying_group_id,
            metric="volume",
            ts=int(b.ts),
            value=float(b.volume),
        )
        n += 3
    # Recompute baseline state per (instrument, metric) once per batch — at
    # the *batch max ts* per (instrument, group) so out-of-order or backfill
    # batches do not compute against an early window (codex Day 6 P1 fix).
    instrument_max_ts: dict[tuple[str, str], int] = {}
    for b in bars_list:
        key = (b.instrument_id, b.underlying_group_id)
        ts = int(b.ts)
        if ts > instrument_max_ts.get(key, 0):
            instrument_max_ts[key] = ts
    for (instrument_id, group_id), max_ts in instrument_max_ts.items():
        for metric in BAR_BASELINE_METRICS:
            update_baseline_from_window(
                conn,
                instrument_id=instrument_id,
                underlying_group_id=group_id,
                metric=metric,
                now_ts=max_ts,
                asset_class=asset_class,
            )
    return n


# ---------------------------------------------------------------------------
# Async offload variant — pure-compute (sort/percentile) runs in a worker
# thread; ALL DB access (append_sample / read_samples_window / upsert) stays
# on the event-loop thread (shared loop-affine conn — concurrency rule).
# ---------------------------------------------------------------------------

# A unit of baseline work after the DB read: (instrument_id, group_id, metric,
# samples, now_ts, lookback_sec). Plain data only — NO sqlite connection.
BaselineComputeItem = tuple[str, str, str, list[float], int, int]


def compute_baselines_batch(
    items: Iterable[BaselineComputeItem],
) -> list[tuple[str, str, BaselineValue]]:
    """PURE compute: sort+percentile each pre-fetched sample window.

    Input is plain data only (no conn) so it is safe to run via
    ``asyncio.to_thread``. Each result is ``(instrument_id, group_id,
    BaselineValue)``; empty sample windows are dropped (mirrors the sync
    ``update_baseline_from_window`` which returns None and writes nothing).

    Identical output to per-item ``compute_baseline`` — only the heavy sort is
    moved off the loop.
    """
    out: list[tuple[str, str, BaselineValue]] = []
    for instrument_id, group_id, metric, samples, now_ts, lookback in items:
        if not samples:
            continue
        bv = compute_baseline(
            metric=metric, samples=samples, updated_ts=now_ts, lookback_sec=lookback
        )
        out.append((instrument_id, group_id, bv))
    return out


async def update_baseline_from_bars_async(
    conn: sqlite3.Connection,
    bars: Iterable[Bar],
    *,
    asset_class: str = "",
) -> int:
    """Async twin of ``update_baseline_from_bars`` — heavy sort off the loop.

    Phase A (loop): append per-bar samples + read each rolling window.
    Phase B (thread): ``asyncio.to_thread(compute_baselines_batch, ...)`` —
        the sort/percentile of every (instrument, metric) window at once.
    Phase C (loop): upsert each resulting ``BaselineValue``.

    The shared conn never enters the worker thread (read + write both happen on
    the loop). Output is identical to the synchronous path.
    """
    bars_list: list[Bar] = list(bars)
    n = 0
    for b in bars_list:
        atr_value = max(0.0, float(b.high) - float(b.low))
        notional = float(b.notional_usd) if b.notional_usd > 0 else float(b.close) * float(b.volume)
        append_sample(
            conn, instrument_id=b.instrument_id,
            underlying_group_id=b.underlying_group_id, metric="atr",
            ts=int(b.ts), value=atr_value,
        )
        append_sample(
            conn, instrument_id=b.instrument_id,
            underlying_group_id=b.underlying_group_id, metric="size",
            ts=int(b.ts), value=float(notional),
        )
        append_sample(
            conn, instrument_id=b.instrument_id,
            underlying_group_id=b.underlying_group_id, metric="volume",
            ts=int(b.ts), value=float(b.volume),
        )
        n += 3
    # Recompute window per (instrument, metric) at the batch-max ts (same rule
    # as the sync path: out-of-order/backfill batches don't compute early).
    instrument_max_ts: dict[tuple[str, str], int] = {}
    for b in bars_list:
        key = (b.instrument_id, b.underlying_group_id)
        ts = int(b.ts)
        if ts > instrument_max_ts.get(key, 0):
            instrument_max_ts[key] = ts
    # Phase A — DB reads stay on the loop (shared conn).
    work: list[BaselineComputeItem] = []
    for (instrument_id, group_id), max_ts in instrument_max_ts.items():
        for metric in BAR_BASELINE_METRICS:
            lookback = _default_lookback(metric)
            samples = read_samples_window(
                conn,
                instrument_id=instrument_id,
                metric=metric,
                window_start_ts=max_ts - lookback,
            )
            if samples:
                work.append((instrument_id, group_id, metric, samples, max_ts, lookback))
    if not work:
        return n
    # Phase B — pure sort/percentile off the loop.
    results = await asyncio.to_thread(compute_baselines_batch, work)
    # Phase C — DB writes back on the loop.
    for instrument_id, group_id, bv in results:
        upsert_baseline_state(
            conn,
            instrument_id=instrument_id,
            underlying_group_id=group_id,
            asset_class=asset_class,
            baseline=bv,
        )
    return n


def ingest_bars(
    conn: sqlite3.Connection,
    bars: Iterable[Bar],
    *,
    asset_class: str = "",
) -> dict[str, int]:
    """Persist bars + baseline samples in one call.

    Returns ``{"bars": N, "baseline_samples": M}``.
    """
    bars_list = list(bars)
    persisted = persist_bars(conn, bars_list)
    baseline_n = update_baseline_from_bars(conn, bars_list, asset_class=asset_class)
    if bars_list:
        symbols = {b.symbol for b in bars_list}
        logger.info(
            "[ingest] bars=%d baseline_samples=%d symbols=%d (asset_class=%s)",
            persisted,
            baseline_n,
            len(symbols),
            asset_class or "<unspec>",
        )
    return {"bars": persisted, "baseline_samples": baseline_n}


async def ingest_bars_async(
    conn: sqlite3.Connection,
    bars: Iterable[Bar],
    *,
    asset_class: str = "",
) -> dict[str, int]:
    """Async twin of ``ingest_bars`` — persist + offloaded baseline recompute.

    ``persist_bars`` (DB write) stays on the loop; the baseline sort/percentile
    batch is offloaded via ``update_baseline_from_bars_async``. Same return
    shape as ``ingest_bars``.
    """
    bars_list = list(bars)
    persisted = persist_bars(conn, bars_list)
    baseline_n = await update_baseline_from_bars_async(
        conn, bars_list, asset_class=asset_class
    )
    if bars_list:
        symbols = {b.symbol for b in bars_list}
        logger.info(
            "[ingest] bars=%d baseline_samples=%d symbols=%d (asset_class=%s)",
            persisted,
            baseline_n,
            len(symbols),
            asset_class or "<unspec>",
        )
    return {"bars": persisted, "baseline_samples": baseline_n}
