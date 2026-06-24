"""WS quote tick writer — in-mem coalesce + 1Hz off-loop batch flush.

Design SSOT: ``.claude/plans/p4_ws_realtime_price_2026-06-01.md`` (M1/M2).

Why this shape (M1 event-loop-stall = top risk):
- The bot's shared sqlite connection is autocommit (``isolation_level=None``).
  An ``executemany INSERT OR REPLACE`` on it would commit per-row (one WAL
  frame flush + fsync each) — the "1 txn" premise collapses. So the flush wraps
  the batch in an explicit ``BEGIN; executemany; COMMIT``.
- All sqlite writes in this codebase run synchronously on the loop thread.
  Running the batch inline would stall the whole event loop (WS recv + tick
  pipeline serialize on the same conn). So the flush snapshots the buffer (no
  await between drain and clear) and offloads the blocking sqlite block via
  ``loop.run_in_executor(None, self._flush_blocking)``.
- A WS-writer-dedicated sqlite connection (same WAL DB, busy_timeout kept) is
  opened so the executor thread never touches the loop-owned conn. The
  single-writer invariant holds because only the bot process opens RW conns;
  dashboard reads use a separate read-only process.

M2: ``on_quote`` (the WS recv callback) is pure in-mem — coalesce dict +
``live_px`` + ``last_ws_monotonic``. It NEVER touches the DB. Writes are the
1Hz flush task's exclusive job.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import time
from collections import deque
from pathlib import Path

from polaris.core.data.schema import QuoteTick
from polaris.core.ticks.types import TickSample
from polaris.storage.schema import connect

logger = logging.getLogger(__name__)

# Per-instrument ring-buffer depth for the live tick window. G4's watcher reads
# the last ~30 ticks (pre_entry_watcher slices ``tick_window[-30:]``), and the P5
# tick-decision engine's ``feature_window`` reads the full ring. 600 ticks is
# ~60-120s of history at ~5-10 ticks/s — enough for the engine's 1/3/10s EWMA
# microstructure features while staying in-mem only (never persisted; the DB keeps
# last-write-wins per PK).
RING_BUFFER_DEPTH = 600

# Execution-default freshness threshold (M6). The LIVE WS mid is the execution
# default for EVERY trade-execution price (entry fill ref, exit/close sizing
# mark, sim-exit mark) when its ``time.monotonic()`` age is below this; an older
# tick (no WS / reconnecting) degrades to the most-recent bar close. Set ABOVE
# the WS reconnect worst-case (BACKOFF_CAP_SEC=30s) so a single reconnect cannot
# flap the execution mark between the WS tick and the bar close. The exit-recalc
# path keeps its own ``WS_EXIT_MARK_FRESH_SEC`` (same value) for the exit TRIGGER
# mark; this constant is the shared default for the remaining execution prices.
LIVE_PRICE_FRESH_SEC = 35.0

# On-disk quote_ticks retention. quote_ticks is a TRANSIENT live-stream cache:
# the live tick engine reads its window from the in-mem ring (above), and the
# only DB consumers are the gate's 60s mark fallback + the dashboard recent-price
# view — so 10 min is already generous (Jin 2026-06-24 "틱은 수분만 저장";
# persisting the full stream for 2h dominated the WAL creep that ENOSPC'd the
# bot). The dedicated WS-writer prunes its OWN table here, right after a batch
# write, on the connection that just held the write lock — so the prune can never
# be lock-starved the way the old separate-connection checkpoint worker was (its
# DELETE failed "database is locked" 74×/session and the table grew unbounded).
QUOTE_TICKS_RETAIN_SEC = 600
# Prune in BOUNDED chunks per flush. A single full-table DELETE (250k rows) lost
# the write-lock race "database is locked" for 60s+ against the busy loop conn and
# the table grew unbounded → ENOSPC; a SMALL chunk slips into the same lock gaps
# the small batch inserts win. Drains any backlog over successive 1 Hz flushes,
# then deletes only the steady-state increment.
_TICK_PRUNE_CHUNK = 2000

_INSERT_SQL = (
    "INSERT OR REPLACE INTO quote_ticks "
    "(instrument_id, venue, symbol, ts, bid, ask, mid, spread_bps, "
    "bid_size, ask_size, last_trade_price, last_trade_size, source) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _row(q: QuoteTick) -> tuple[object, ...]:
    return (
        q.instrument_id,
        q.venue,
        q.symbol,
        q.ts,
        q.bid,
        q.ask,
        q.mid,
        q.spread_bps,
        q.bid_size,
        q.ask_size,
        q.last_trade_price,
        q.last_trade_size,
        q.source,
    )


def live_or_bar_price(
    quote_writer: QuoteTickWriter | None,
    instrument_id: str,
    bar_fallback: float,
    *,
    fresh_sec: float = LIVE_PRICE_FRESH_SEC,
) -> float:
    """Execution-default price: the LIVE WS mid, else ``bar_fallback``.

    The single rule for trade EXECUTION (entry fill ref, exit/close sizing mark,
    sim-exit mark): act at the live WebSocket price by default, falling back to
    the most-recent bar close ONLY when no fresh tick exists for the symbol.
    Strategy signals / indicators / regime stay on bars (analysis) — this is the
    execution price they act AT, not the judgment they act ON.

    Returns the live mid when ``quote_writer`` carries a tick with ``mid > 0`` and
    ``time.monotonic()`` age ``< fresh_sec`` (M6: monotonic clock, never venue
    ts), else ``bar_fallback`` (graceful degrade — never halts on a missing tick,
    AGGRESSIVE/flow_not_block invariant). 0 DB hits (reads the in-mem ring).
    """
    if quote_writer is not None:
        px = quote_writer.live_px(instrument_id)
        if px is not None:
            mid, last_ws_monotonic = px
            if mid > 0.0 and time.monotonic() - last_ws_monotonic < fresh_sec:
                return mid
    return bar_fallback


class QuoteTickWriter:
    """Coalesce WS quote ticks in memory; flush to sqlite ~1Hz off the loop.

    Lifecycle:
        w = QuoteTickWriter(db_path)
        w.on_quote(tick)            # called from WS recv callbacks (in-mem only)
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
        # Coalesce: last-write-wins per instrument_id (PK collisions dropped).
        self._buf: dict[str, QuoteTick] = {}
        # Process-shared live price view: instrument_id -> (mid, last_ws_monotonic).
        # Consumers read this in-mem (0 DB hits). Updated on every on_quote.
        self._live_px: dict[str, tuple[float, float]] = {}
        # Short per-instrument tick history (newest-last) for G4's tick_window.
        # In-mem only (M2: on_quote never touches the DB); capped at
        # ``RING_BUFFER_DEPTH`` so it stays O(1) per tick and bounded in size.
        self._ring: dict[str, deque[QuoteTick]] = {}
        # Dedicated WS-writer connection (opened lazily on first flush, in the
        # executor thread — sqlite objects are thread-affine).
        self._conn: sqlite3.Connection | None = None
        self.flush_count = 0
        self.rows_written = 0

    # ------------------------------------------------------------------
    # In-mem path (WS recv callback) — M2: NO DB access here.
    # ------------------------------------------------------------------

    def on_quote(self, tick: QuoteTick) -> None:
        """Record one tick in memory (coalesce + live_px + ring). Never awaits."""
        self._buf[tick.instrument_id] = tick
        self._live_px[tick.instrument_id] = (tick.mid, time.monotonic())
        ring = self._ring.get(tick.instrument_id)
        if ring is None:
            ring = deque(maxlen=RING_BUFFER_DEPTH)
            self._ring[tick.instrument_id] = ring
        ring.append(tick)

    def live_px(self, instrument_id: str) -> tuple[float, float] | None:
        """Return ``(mid, last_ws_monotonic)`` for ``instrument_id`` or None.

        ``last_ws_monotonic`` is a ``time.monotonic()`` stamp (M6: staleness is
        judged on the monotonic clock, never venue ts).
        """
        return self._live_px.get(instrument_id)

    def recent_ticks(self, instrument_id: str) -> list[dict[str, object]]:
        """Return the last ~30 ticks (newest-last) as G4 ``tick_window`` dicts.

        Each dict carries ``ts`` (venue ts), ``bid``, ``ask``, ``mid`` — the keys
        ``g4_shadow_inputs_from_payload`` reads. Empty list when no WS history yet
        (G4 then treats freshness as unknown, never a manufactured stale KILL).
        """
        ring = self._ring.get(instrument_id)
        if not ring:
            return []
        return [
            {"ts": t.ts, "bid": t.bid, "ask": t.ask, "mid": t.mid}
            for t in ring
        ]

    def feature_window(self, instrument_id: str) -> list[TickSample]:
        """Return the full live ring (oldest→newest) as ``TickSample`` rows.

        Unlike ``recent_ticks`` (4 keys, G4-compatibility frozen), this exposes
        the full microstructure — bid/ask sizes + last-trade — that the P5
        tick-decision engine's feature module needs. ``spread_bps`` is the stored
        value, or ``(ask - bid) / mid * 1e4`` recomputed when the stored value is
        missing (0.0) and ``mid > 0``. Empty list when no WS history yet.
        """
        ring = self._ring.get(instrument_id)
        if not ring:
            return []
        return [
            TickSample(
                ts=t.ts,
                bid=t.bid,
                ask=t.ask,
                mid=t.mid,
                bid_size=t.bid_size,
                ask_size=t.ask_size,
                last_trade_price=t.last_trade_price,
                last_trade_size=t.last_trade_size,
                spread_bps=(
                    t.spread_bps
                    if t.spread_bps
                    else ((t.ask - t.bid) / t.mid * 1e4 if t.mid > 0 else 0.0)
                ),
            )
            for t in ring
        ]

    # ------------------------------------------------------------------
    # Flush path (1Hz task) — snapshot (no await between drain+clear) then
    # offload the blocking sqlite block to the default executor (M1).
    # ------------------------------------------------------------------

    async def run_flush_loop(self, stop_evt: asyncio.Event) -> None:
        """Flush coalesced ticks every ``flush_interval_sec`` until stopped.

        On ``stop_evt`` a final flush drains whatever remains. The loop never
        raises out of a flush — a write error keeps the buffer for the next
        attempt (graceful, never halts the bot).
        """
        loop = asyncio.get_running_loop()
        while not stop_evt.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_evt.wait(), timeout=self._flush_interval)
            await self._flush_once(loop)
        # Final drain on teardown.
        await self._flush_once(loop)

    async def _flush_once(self, loop: asyncio.AbstractEventLoop) -> None:
        # Snapshot + clear with NO await in between → no tick lost to a race:
        # on_quote runs on the same loop thread, so between this dict() copy and
        # clear() no callback can interleave (single-threaded loop).
        if not self._buf:
            return
        snapshot = list(self._buf.values())
        self._buf.clear()
        try:
            await loop.run_in_executor(None, self._flush_blocking, snapshot)
        except Exception:  # noqa: BLE001 — write must never halt the bot (AGGRESSIVE)
            logger.exception(
                "[quote_writer] flush of %d ticks failed — dropping batch", len(snapshot)
            )

    def _flush_blocking(self, ticks: list[QuoteTick]) -> None:
        """Blocking sqlite write — runs in the executor thread, never the loop.

        Wraps the batch in an explicit transaction so the autocommit conn does
        ONE WAL commit for the whole batch (M1a), not one per row.
        """
        conn = self._ensure_conn()
        rows = [_row(q) for q in ticks]
        conn.execute("BEGIN")
        try:
            conn.executemany(_INSERT_SQL, rows)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        self.flush_count += 1
        self.rows_written += len(rows)
        # Prune the transient stream on THIS conn in a BOUNDED chunk (see
        # _TICK_PRUNE_CHUNK). The dedicated writer wins the write lock for its small
        # inserts, and a small chunked DELETE wins it the same way — a single
        # full-table DELETE did NOT (it lost the lock race for 60s+ and the table
        # grew unbounded → ENOSPC). Bare autocommit (no explicit BEGIN/COMMIT) keeps
        # the batch insert above the single tested transaction; best-effort so a
        # lock blip just retries on the next flush.
        cutoff = int(time.time()) - QUOTE_TICKS_RETAIN_SEC
        with contextlib.suppress(sqlite3.Error):
            conn.execute(
                "DELETE FROM quote_ticks WHERE rowid IN "
                "(SELECT rowid FROM quote_ticks WHERE ts < ? LIMIT ?)",
                (cutoff, _TICK_PRUNE_CHUNK),
            )

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # Dedicated WS-writer conn: same WAL DB, busy_timeout preserved
            # (connect() applies the PRAGMAs). Opened in the executor thread.
            self._conn = connect(self._db_path)
        return self._conn

    def close(self) -> None:
        """Close the dedicated connection (call after the flush loop ends)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
