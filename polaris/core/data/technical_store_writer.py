"""Technical-store write off-loader — in-mem LWW coalesce + 1Hz off-loop flush.

STALL fix #88 (the #74 'shared-conn blocker' re-introduced for the ④ #12 technical
store). The store WRITE previously ran SYNCHRONOUSLY on the loop thread through the
shared tick connection: ``upsert_technicals(conn, ...)`` was called inline in the
per-(venue,symbol,timeframe) focus fan-out. That heavy writer contended the WAL
single-writer lock with the 1 Hz ``quote_writer`` flush → SQLITE_BUSY → the
``busy_timeout`` (5 s) stalls the tick cadence ([[feedback_db_lock_is_architecture_signal]]
— the STALL fix is a SEPARATION, never a hot-path patch).

This writer decouples it with the SAME shape ``QuoteTickWriter`` already uses (the
#74-reviewed pattern):

* ``record`` is pure in-mem on the loop thread — it coalesces the just-extracted
  indicator snapshot into a buffer keyed on ``(instrument_id, bar_interval)``
  (LAST-WRITE-WINS, so the buffer is bounded by focus × timeframes between drains)
  and NEVER touches the DB. No ``await``, no I/O — the loop thread is never blocked.
* ``run_flush_loop`` drains the buffer ~1 Hz: it snapshots + clears with no
  ``await`` in between (single-threaded loop → race-free) and OFF-LOADS the
  blocking sqlite write to the default executor via
  ``loop.run_in_executor(None, self._flush_blocking)`` — so the loop thread never
  runs the ``executemany``.
* A DEDICATED writer connection (same WAL DB, ``busy_timeout`` preserved by
  ``connect``) is opened on the executor thread, so the executor never touches the
  loop-owned conn. The WAL single-writer invariant holds because only one flush
  runs at a time and only the bot process opens RW conns.
* The batch is wrapped in ONE explicit ``BEGIN; executemany; COMMIT`` so the
  autocommit conn does ONE WAL commit for the whole batch (not one per write).

EVIDENCE-ONLY / flow_not_block: nothing here gates an entry/size/exit or touches a
sizing multiplier. The technical store keeps being written (judge/probe evidence) —
only the WHERE (executor thread + dedicated conn) changes, not the WHAT.
degrade-never-crash: a write fault is logged + the batch dropped; the loop survives.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from polaris.storage.schema import connect

__all__ = ["TechnicalStoreWriter"]

logger = logging.getLogger(__name__)

# One row per indicator (PK = instrument_id + bar_interval + indicator), so a
# re-write OVERWRITES in place — the table is BOUNDED (instruments × intervals ×
# indicators), never an append. Mirrors ``upsert_technicals`` (the store primitive
# this off-loader writes through); kept here so the flush is one ``executemany``.
_UPSERT_SQL = (
    "INSERT INTO ticker_technicals "
    "(instrument_id, bar_interval, indicator, value, computed_ts, source_bar_ts) "
    "VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(instrument_id, bar_interval, indicator) "
    "DO UPDATE SET value=excluded.value, "
    "computed_ts=excluded.computed_ts, "
    "source_bar_ts=excluded.source_bar_ts"
)


@dataclass(slots=True)
class _TechEntry:
    """A coalesced indicator snapshot for one (instrument, interval) — LWW."""

    values: dict[str, float]
    computed_ts: int
    source_bar_ts: int


class TechnicalStoreWriter:
    """Coalesce technical-store writes in memory; flush to sqlite ~1Hz off the loop.

    Lifecycle (mirrors ``QuoteTickWriter``):
        w = TechnicalStoreWriter(db_path)
        w.record(instrument_id=..., bar_interval=..., values=..., ...)  # in-mem
        task = asyncio.create_task(w.run_flush_loop(stop_evt))
        ...
        stop_evt.set(); await task  # final flush on teardown
        w.close()                   # close the dedicated conn
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        flush_interval_sec: float = 1.0,
    ) -> None:
        self._db_path = db_path
        self._flush_interval = flush_interval_sec
        # Coalesce: last-write-wins per (instrument_id, bar_interval). A re-record
        # overwrites the buffered snapshot, so the buffer is bounded by the number
        # of distinct (focus symbol × timeframe) pairs seen between drains.
        self._buf: dict[tuple[str, str], _TechEntry] = {}
        # Dedicated writer connection (opened lazily on first flush, in the
        # executor thread — sqlite objects are thread-affine).
        self._conn: sqlite3.Connection | None = None
        self.flush_count = 0
        self.rows_written = 0

    # ------------------------------------------------------------------
    # In-mem path (loop thread) — NO DB access here.
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        instrument_id: str,
        bar_interval: str,
        values: dict[str, float],
        computed_ts: int,
        source_bar_ts: int,
    ) -> None:
        """Buffer one (instrument, interval) indicator snapshot. Never awaits / I/O.

        Pure in-mem on the loop thread: it replaces the buffered snapshot for the
        key (LWW), so a faster tick cadence than the flush can never grow the
        buffer past the live focus × timeframe set. An empty ``values`` is a no-op
        (no NULL noise — matches ``upsert_technicals``).
        """
        if not values:
            return
        self._buf[(instrument_id, bar_interval)] = _TechEntry(
            values=dict(values),
            computed_ts=int(computed_ts),
            source_bar_ts=int(source_bar_ts),
        )

    # ------------------------------------------------------------------
    # Flush path (1Hz task) — snapshot (no await between drain+clear) then
    # offload the blocking sqlite block to the default executor.
    # ------------------------------------------------------------------

    async def run_flush_loop(self, stop_evt: asyncio.Event) -> None:
        """Flush buffered snapshots every ``flush_interval_sec`` until stopped.

        On ``stop_evt`` a final flush drains whatever remains. The loop never
        raises out of a flush — a write error drops that batch and the next
        attempt proceeds (graceful, never halts the bot).
        """
        loop = asyncio.get_running_loop()
        while not stop_evt.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_evt.wait(), timeout=self._flush_interval)
            await self._flush_once(loop)
        # Final drain on teardown.
        await self._flush_once(loop)

    async def _flush_once(self, loop: asyncio.AbstractEventLoop) -> None:
        # Snapshot + clear with NO await in between → no record lost to a race:
        # record() runs on the same loop thread, so between this copy and clear()
        # no callback can interleave (single-threaded loop). The snapshot keeps the
        # (instrument, interval) keys so the flush can build the per-indicator rows.
        if not self._buf:
            return
        snapshot = list(self._buf.items())
        self._buf.clear()
        try:
            await loop.run_in_executor(None, self._flush_blocking, snapshot)
        except Exception:  # noqa: BLE001 — write must never halt the bot (AGGRESSIVE)
            logger.exception(
                "[tech_store] flush of %d entries failed — dropping batch",
                len(snapshot),
            )

    def _flush_blocking(
        self, entries: list[tuple[tuple[str, str], _TechEntry]]
    ) -> None:
        """Blocking sqlite write — runs in the executor thread, never the loop.

        Flattens every buffered entry to its per-indicator rows and writes them in
        ONE explicit ``BEGIN; executemany; COMMIT`` so the autocommit conn does
        ONE WAL commit for the whole batch (not one per write). A sqlite error
        (incl. a missing table — degrade-never-crash) propagates to ``_flush_once``
        which logs + drops the batch; the loop survives.
        """
        rows = [
            (
                instrument_id, bar_interval, name, float(v),
                entry.computed_ts, entry.source_bar_ts,
            )
            for (instrument_id, bar_interval), entry in entries
            for name, v in entry.values.items()
        ]
        if not rows:
            return
        conn = self._ensure_conn()
        conn.execute("BEGIN")
        try:
            conn.executemany(_UPSERT_SQL, rows)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        self.flush_count += 1
        self.rows_written += len(rows)

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # Dedicated writer conn: same WAL DB, busy_timeout preserved
            # (connect() applies the PRAGMAs). Opened in the executor thread.
            self._conn = connect(self._db_path)
        return self._conn

    def close(self) -> None:
        """Close the dedicated connection (call after the flush loop ends)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
