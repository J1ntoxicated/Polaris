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
import math
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from polaris.core.data.baseline import (
    _default_lookback,
    append_samples_batch,
    compute_baseline,
    read_samples_window,
    update_baseline_from_window,
    upsert_baseline_state,
)
from polaris.core.data.schema import Bar, BaselineValue
from polaris.storage.db_writer import DBWriter, dbwriter_enabled
from polaris.storage.schema import connect

logger = logging.getLogger(__name__)

__all__ = [
    "BAR_BASELINE_METRICS",
    "compute_baselines_batch",
    "ingest_bars",
    "ingest_bars_async",
    "ingest_bars_offloaded",
    "persist_bars",
    "persist_bars_offloaded",
    "update_baseline_from_bars",
    "update_baseline_from_bars_async",
]

# Metrics derivable from a closed bar (Q2 spec).
BAR_BASELINE_METRICS: tuple[str, ...] = ("atr", "size", "volume")


def _bar_ohlc_is_persistable(b: Bar) -> bool:
    """True iff every OHLC field is a finite, positive price.

    SQLite stores a Python ``float('nan')`` as NULL, so a NaN open/high/low/close
    (yfinance hands these on thin tickers and some Capital index epics —
    J225/HK50/AU200) would trip the bars NOT NULL constraint and, unguarded,
    fail the WHOLE INSERT batch (taking every GOOD bar down with it). This
    per-row predicate is the VENUE-AGNOSTIC last line of defence: every persist
    path (focus ingest, static-ground walk, on-demand refetch) flows through
    ``persist_bars``, so guarding here covers Alpaca/Capital/OKX/Yahoo at once.
    flow_not_block: it only WIDENS what survives ingest — no entry/size/exit.
    """
    return (
        math.isfinite(b.open) and b.open > 0.0
        and math.isfinite(b.high) and b.high > 0.0
        and math.isfinite(b.low) and b.low > 0.0
        and math.isfinite(b.close) and b.close > 0.0
    )


def _sanitize_aux(value: float) -> float:
    """Coerce a non-OHLC numeric (volume/notional/vwap/quote) to a finite float.

    A non-finite VOLUME (etc.) must not drop an otherwise-valid OHLC bar nor be
    stored as NULL — sanitize to 0.0 (mirrors the venue adapters' ``_to_float``).
    """
    return value if math.isfinite(value) else 0.0


def persist_bars(conn: sqlite3.Connection, bars: Iterable[Bar]) -> int:
    """Idempotent insert into the ``bars`` table — batch-survivable.

    Returns the number of bars *persisted*. Duplicates upsert in place via
    ``INSERT OR REPLACE``, so the count is "rows written" not
    "rows newly inserted" (codex Day 6 P2 contract clarification).

    Per-row robustness (Jin 2026-06-27 "데이터 proactive"): a row whose OHLC is
    not finite+positive is SKIPPED (``_bar_ohlc_is_persistable``) so a single
    malformed candle in a batch never fails the whole INSERT and never rolls back
    the good bars beside it (the live failure: one NaN-open Alpaca bar / NaN-close
    Capital index bar nuked the symbol's whole batch → DB held 0 such bars). The
    auxiliary numerics (volume/notional/vwap/quotes) are sanitized to a finite
    value rather than dropping the bar. flow_not_block — data layer only.

    Bulk-write lever (forensic wf_1f586d0a #1): the surviving rows are issued
    via ONE ``conn.executemany()`` instead of a per-row ``conn.execute()``
    loop — same rows, same INSERT OR REPLACE statement, same (caller-owned)
    transaction; only the Python/C round-trip overhead per row is cut. This
    is the dominant write-lock hold time on a large DBWriter-batched ingest
    job (each job already runs inside the writer's own SAVEPOINT/BEGIN).
    """
    rows: list[tuple[object, ...]] = []
    for b in bars:
        if not _bar_ohlc_is_persistable(b):
            logger.debug(
                "[ingest] dropped non-finite OHLC bar %s/%s ts=%s "
                "(o=%r h=%r l=%r c=%r)",
                b.instrument_id, b.bar_interval, b.ts,
                b.open, b.high, b.low, b.close,
            )
            continue
        rows.append((
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
            _sanitize_aux(float(b.volume)),
            _sanitize_aux(float(b.notional_usd)),
            int(b.trade_count),
            _sanitize_aux(float(b.vwap)),
            _sanitize_aux(float(b.bid_close)),
            _sanitize_aux(float(b.ask_close)),
            _sanitize_aux(float(b.spread_bps_close)),
            b.source,
        ))
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO bars
            (instrument_id, underlying_group_id, venue, symbol, bar_interval,
             ts, open, high, low, close, volume, notional_usd, trade_count,
             vwap, bid_close, ask_close, spread_bps_close, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


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

    Bulk-write lever (forensic wf_1f586d0a #1): all 3*N per-bar sample rows
    are built as a plain list and appended via ONE ``append_samples_batch``
    (``executemany``) call instead of 3*N individual ``append_sample``
    (``execute``) calls — same rows, same INSERT OR REPLACE semantics, same
    (caller-owned) transaction. The per-(instrument,group) window recompute
    below is UNCHANGED (still one ``update_baseline_from_window`` call per
    unique key per batch).
    """
    bars_list: list[Bar] = list(bars)
    sample_rows: list[tuple[str, str, str, int, float]] = []
    for b in bars_list:
        atr_value = max(0.0, float(b.high) - float(b.low))
        notional = float(b.notional_usd) if b.notional_usd > 0 else float(b.close) * float(b.volume)
        ts = int(b.ts)
        sample_rows.append((b.instrument_id, b.underlying_group_id, "atr", ts, atr_value))
        sample_rows.append((b.instrument_id, b.underlying_group_id, "size", ts, float(notional)))
        sample_rows.append((b.instrument_id, b.underlying_group_id, "volume", ts, float(b.volume)))
    n = append_samples_batch(conn, sample_rows)
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

    Bulk-write lever (forensic wf_1f586d0a #1): same ``append_samples_batch``
    (one ``executemany``) swap as the sync path — see
    ``update_baseline_from_bars`` docstring.
    """
    bars_list: list[Bar] = list(bars)
    sample_rows: list[tuple[str, str, str, int, float]] = []
    for b in bars_list:
        atr_value = max(0.0, float(b.high) - float(b.low))
        notional = float(b.notional_usd) if b.notional_usd > 0 else float(b.close) * float(b.volume)
        ts = int(b.ts)
        sample_rows.append((b.instrument_id, b.underlying_group_id, "atr", ts, atr_value))
        sample_rows.append((b.instrument_id, b.underlying_group_id, "size", ts, float(notional)))
        sample_rows.append((b.instrument_id, b.underlying_group_id, "volume", ts, float(b.volume)))
    n = append_samples_batch(conn, sample_rows)
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
    # Phase B — pure sort/percentile off the loop, CHUNKED. compute_baselines_batch
    # is pure-Python sorted()+percentile, so it holds the GIL for its entire run —
    # a SINGLE to_thread call over the whole work set blocks the event loop (the
    # tick engine + WS recv) for the full seed, because the worker thread holds the
    # GIL the whole time (to_thread only helps for I/O-bound work that releases it).
    # Live: a universe refresh that seeds 30+ fresh symbols' 7-day windows stalled
    # the loop for minutes (tick-engine STALL gap up to 225s). Chunking + awaiting
    # each small chunk lets the real-time path run BETWEEN chunks (the GIL is held
    # only for ~8 windows at a time). Output is identical — same items, same order.
    results: list[tuple[str, str, BaselineValue]] = []
    _BASELINE_CHUNK = 8
    for i in range(0, len(work), _BASELINE_CHUNK):
        results.extend(
            await asyncio.to_thread(compute_baselines_batch, work[i : i + _BASELINE_CHUNK])
        )
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


# ---------------------------------------------------------------------------
# Full off-loop ingest — STALL residual fix (#90). The ENTIRE per-1m-tick
# persist + baseline-append DB write is moved to a worker thread on a DEDICATED
# connection, so the event loop (tick engine + WS recv) is never held for the
# 29k-60k row-by-row sqlite executes the live log brackets to the STALL gap.
#
# This is the #74 / #88 / retention_producer pattern: ``asyncio.to_thread`` +
# a thread-confined conn opened from the db PATH (never the loop-owned conn) +
# snapshot inputs (the bars list is already materialized). Behaviour-identical
# to ``ingest_bars`` — same bars, same baselines, same order — only WHERE the
# write runs changes (worker thread instead of the loop). WAL single-writer is
# safe because the caller awaits each batch sequentially, so only one ingest
# worker ever touches the dedicated conn at a time. degrade-never-crash: a
# sqlite fault is swallowed and ``{}`` returned so the next tick retries.
# ---------------------------------------------------------------------------


def _db_path_from_conn(conn: sqlite3.Connection) -> str | None:
    """Return the on-disk path of a file-backed conn, or ``None`` for in-memory.

    ``PRAGMA database_list`` reports the path SQLite itself uses to open the
    ``main`` database, so a dedicated ``connect(path)`` re-opens the SAME
    physical file (WAL makes committed writes visible to the loop conn). An
    in-memory DB (``:memory:`` / tests) reports an EMPTY path → ``None``, which
    signals the caller to keep the write on the loop (no offload possible).
    """
    try:
        for _seq, name, file in conn.execute("PRAGMA database_list").fetchall():
            if name == "main":
                return str(file) if file else None
    except sqlite3.Error:
        return None
    return None


def _ingest_blocking(
    db_path: str | Path,
    bars: list[Bar],
    *,
    asset_class: str = "",
) -> dict[str, int]:
    """Blocking persist + baseline ingest on a DEDICATED conn (worker thread).

    Opens its OWN WAL connection from ``db_path`` (never the loop-owned conn —
    sqlite handles are thread-affine), runs the FULL synchronous ingest
    (``persist_bars`` + ``update_baseline_from_bars``), and closes the handle.
    Returns the same ``{"bars": N, "baseline_samples": M}`` shape as
    ``ingest_bars``. Any sqlite fault returns ``{"bars": 0, "baseline_samples":
    0}`` (degrade-never-crash) so the offload caller never raises into the loop.
    """
    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        logger.warning("[ingest] offload conn open failed (degrade): %r", exc)
        return {"bars": 0, "baseline_samples": 0}
    try:
        return ingest_bars(conn, bars, asset_class=asset_class)
    except sqlite3.Error as exc:
        logger.warning("[ingest] offload write failed (degrade): %r", exc)
        return {"bars": 0, "baseline_samples": 0}
    finally:
        conn.close()


async def ingest_bars_offloaded(
    db_path: str | Path,
    bars: Iterable[Bar],
    *,
    asset_class: str = "",
    db_writer: DBWriter | None = None,
) -> dict[str, int]:
    """Off-loop twin of ``ingest_bars`` — persist + baseline off the loop.

    Default (``db_writer=None``) is BYTE-IDENTICAL to the pre-split path: the
    bars list is snapshotted (plain data, no conn) and the whole write runs via
    ``asyncio.to_thread`` on a DEDICATED connection.

    db-writer-reader-split (opt-in): when ``db_writer`` is supplied and the
    kill switch reads enabled, the write is submitted as a DURABLE job to the
    shared single-RW-conn writer instead of opening a competing dedicated
    conn — this also folds ``persist_bars`` + ``update_baseline_from_bars``'s
    previously per-statement autocommits into ONE batched commit (the
    DBWriter's per-job SAVEPOINT lives inside its batch transaction).
    """
    bars_list = list(bars)
    if not bars_list:
        return {"bars": 0, "baseline_samples": 0}
    if db_writer is not None and dbwriter_enabled():
        result = {"bars": 0, "baseline_samples": 0}

        def _job(conn: sqlite3.Connection) -> None:
            result.update(ingest_bars(conn, bars_list, asset_class=asset_class))

        future = db_writer.submit(_job, durable=True, label="ingest_bars")
        assert future is not None
        try:
            await asyncio.wrap_future(future)
        except Exception as exc:  # noqa: BLE001 — degrade-never-crash (offload fault)
            logger.warning("[ingest] db_writer offload failed (degrade): %r", exc)
            return {"bars": 0, "baseline_samples": 0}
        return result
    return await asyncio.to_thread(
        _ingest_blocking, db_path, bars_list, asset_class=asset_class
    )


def _persist_blocking(db_path: str | Path, bars: list[Bar]) -> int:
    """Blocking ``persist_bars`` on a DEDICATED conn (worker thread).

    The higher-timeframe (non-1m) path persists bars WITHOUT a baseline
    recompute (minute-windowed baselines must not see 15m/1H/1D bars). Mirrors
    ``_ingest_blocking`` minus the baseline append. Fail-open: a sqlite fault
    returns 0 so the next tick retries.
    """
    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        logger.warning("[ingest] offload persist conn open failed (degrade): %r", exc)
        return 0
    try:
        return persist_bars(conn, bars)
    except sqlite3.Error as exc:
        logger.warning("[ingest] offload persist failed (degrade): %r", exc)
        return 0
    finally:
        conn.close()


async def persist_bars_offloaded(
    db_path: str | Path,
    bars: Iterable[Bar],
    *,
    db_writer: DBWriter | None = None,
) -> int:
    """Off-loop twin of ``persist_bars`` — higher-timeframe persist off the loop.

    Default (``db_writer=None``) is BYTE-IDENTICAL to the pre-split path
    (worker thread + dedicated connection). db-writer-reader-split (opt-in):
    see ``ingest_bars_offloaded`` docstring — same durable-submit shape.
    """
    bars_list = list(bars)
    if not bars_list:
        return 0
    if db_writer is not None and dbwriter_enabled():
        result = {"n": 0}

        def _job(conn: sqlite3.Connection) -> None:
            result["n"] = persist_bars(conn, bars_list)

        future = db_writer.submit(_job, durable=True, label="persist_bars")
        assert future is not None
        try:
            await asyncio.wrap_future(future)
        except Exception as exc:  # noqa: BLE001 — degrade-never-crash (offload fault)
            logger.warning("[ingest] db_writer persist offload failed (degrade): %r", exc)
            return 0
        return result["n"]
    return await asyncio.to_thread(_persist_blocking, db_path, bars_list)
