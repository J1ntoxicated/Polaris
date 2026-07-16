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
import os
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Final

from polaris.core.data._tick_activation import (
    ACTIVATION_MAX_SYMBOLS,
    _SymbolActivation,
)
from polaris.core.data.schema import QuoteTick
from polaris.core.ticks.types import TickSample
from polaris.storage.db_writer import DBWriter, dbwriter_enabled
from polaris.storage.schema import connect

# ``ACTIVATION_MAX_SYMBOLS`` is re-exported (the #39 per-symbol accumulator + its
# cap live in ``_tick_activation`` for the ≤500-LOC split) so the existing
# ``quote_writer.ACTIVATION_MAX_SYMBOLS`` import surface is unchanged.
__all__ = [
    "ACTIVATION_MAX_SYMBOLS",
    "QUOTE_SANITY_PCT_DEFAULT",
    "QuoteTickWriter",
    "live_or_bar_price",
    "quote_sanity_pct",
]

logger = logging.getLogger(__name__)

# Per-instrument ring-buffer depth for the live tick window. G3's (former G4,
# relocated P2a group A) crossed-book/stale-book check reads the tick window
# via ``recent_ticks``, and the P5 tick-decision engine's ``feature_window``
# reads the full ring. 600 ticks is ~60-120s of history at ~5-10 ticks/s —
# enough for the engine's 1/3/10s EWMA microstructure features while staying
# in-mem only (never persisted; the DB keeps
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

# Quote sanity guard (P2a — STETH phantom forensic 2026-07-16: a degraded OKX
# demo book let the WS mid drift -9% off the true market while still passing
# the freshness check above, booking a phantom -15R close). When the caller
# supplies a real bar close as ``bar_ref``, a fresh mid that diverges from it
# by more than this fraction is DISTRUSTED (bar_ref returned instead) — a
# hygiene guard, not a block: it only widens which price wins, never halts a
# trade. ``POLARIS_QUOTE_SANITY_PCT`` env-injectable; unset/unparsable/
# non-positive falls back to the default (same shape as ``time_stop_k_mult``).
QUOTE_SANITY_PCT_ENV: Final[str] = "POLARIS_QUOTE_SANITY_PCT"
QUOTE_SANITY_PCT_DEFAULT: Final[float] = 0.03


def quote_sanity_pct(env_value: str | None = None) -> float:
    """Max fractional |mid/bar_ref - 1| before a fresh mid is distrusted."""
    raw = os.getenv(QUOTE_SANITY_PCT_ENV) if env_value is None else env_value
    if raw is None or raw.strip() == "":
        return QUOTE_SANITY_PCT_DEFAULT
    try:
        parsed = float(raw.strip())
    except ValueError:
        return QUOTE_SANITY_PCT_DEFAULT
    return parsed if parsed > 0.0 else QUOTE_SANITY_PCT_DEFAULT

# A flush that hits "database is locked/busy" is EXPECTED WAL backpressure under
# the accepted checkpoint trade (see production_paper_loop._wal_checkpoint_producer),
# NOT a fault — quote_ticks is a single-row LWW table, so a dropped batch only
# defers the latest price for those instruments to the next 1 Hz flush. Emit at
# most one WARNING summary per this interval instead of an ERROR+traceback per
# drop (the per-second storm dominated the daily ops_alerts count + bloated the log).
_LOCK_WARN_INTERVAL_SEC = 60.0

_INSERT_SQL = (
    "INSERT OR REPLACE INTO quote_ticks "
    "(instrument_id, venue, symbol, ts, bid, ask, mid, spread_bps, "
    "bid_size, ask_size, last_trade_price, last_trade_size, source) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

# tick_inflow rollup written in the SAME flush txn as the quote rows (skew 0).
# Sentinel S6 reads this instead of a COUNT over quote_ticks (which is now a
# single-row LWW table). Design: vault/50_research/debates/tick_stream_decouple_2026-06-24.md.
_INFLOW_UPSERT_SQL = (
    "INSERT INTO tick_inflow "
    "(venue, last_tick_ts, ticks_600s, max_flow_size_600s, window_started_at) "
    "VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(venue) DO UPDATE SET "
    "last_tick_ts=excluded.last_tick_ts, "
    "ticks_600s=excluded.ticks_600s, "
    "max_flow_size_600s=excluded.max_flow_size_600s, "
    "window_started_at=excluded.window_started_at"
)

# Rolling tick-rate window: 10 buckets × 60s = 600s (matches Sentinel S6's
# s6_window_sec default). Bounded per venue so the in-mem state is O(venues).
_INFLOW_WINDOW_SEC = 600
_INFLOW_BUCKET_SEC = 60


class _VenueInflow:
    """Per-venue rolling tick-rate / flow-size accumulator (in-mem, loop thread).

    Holds 60s buckets keyed on ``ts // 60`` (each: count + max bid+ask size),
    the max ts ever seen (``last_tick_ts``), and the first ts ever seen
    (``window_started_at`` — frozen, so a restart resets it and Sentinel S6a can
    report WARMING until 600s of window has accrued). ``rollup`` prunes buckets
    older than 600s before the latest tick and returns the durable row Sentinel
    reads. Pure in-mem; touched only on the loop thread (on_quote + flush
    snapshot), so it is race-free with the executor-thread DB write.
    """

    __slots__ = ("buckets", "last_tick_ts", "window_started_at")

    def __init__(self) -> None:
        self.buckets: dict[int, tuple[int, float]] = {}
        self.last_tick_ts: int = 0
        self.window_started_at: int = 0

    def add(self, ts: int, flow_size: float) -> None:
        if self.window_started_at == 0:
            self.window_started_at = ts
        if ts > self.last_tick_ts:
            self.last_tick_ts = ts
        key = ts // _INFLOW_BUCKET_SEC
        count, max_size = self.buckets.get(key, (0, 0.0))
        self.buckets[key] = (count + 1, max(max_size, flow_size))

    def rollup(self) -> tuple[int, int, float, int]:
        """Return ``(last_tick_ts, ticks_600s, max_flow_size_600s, window_started_at)``.

        Prunes buckets whose 60s slot ends before ``last_tick_ts - 600s`` so the
        rate/size reflect only the trailing 600s window.
        """
        floor_key = (self.last_tick_ts - _INFLOW_WINDOW_SEC) // _INFLOW_BUCKET_SEC
        for key in [k for k in self.buckets if k < floor_key]:
            del self.buckets[key]
        ticks = sum(c for c, _ in self.buckets.values())
        max_size = max((s for _, s in self.buckets.values()), default=0.0)
        return self.last_tick_ts, ticks, max_size, self.window_started_at


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
    bar_ref: float | None = None,
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

    ``bar_ref`` (quote sanity guard, optional — pass ONLY a genuine bar close,
    never a fallback placeholder): when supplied and positive, a fresh mid that
    diverges from it by more than ``quote_sanity_pct()`` is DISTRUSTED — a
    degraded order book (STETH phantom forensic 2026-07-16) can hold ``mid > 0``
    and pass the freshness check while being far from the real market. On
    divergence this returns ``bar_ref`` (not the mid) and bumps
    ``quote_writer.quote_sanity_rejects`` + logs a WARNING. Hygiene guard, not a
    block: it never halts a trade, only chooses which price wins.
    """
    if quote_writer is not None:
        px = quote_writer.live_px(instrument_id)
        if px is not None:
            mid, last_ws_monotonic = px
            if mid > 0.0 and time.monotonic() - last_ws_monotonic < fresh_sec:
                if bar_ref is not None and bar_ref > 0.0:
                    divergence = abs(mid / bar_ref - 1.0)
                    if divergence > quote_sanity_pct():
                        quote_writer.quote_sanity_rejects += 1
                        logger.warning(
                            "[quote-sanity] %s mid=%.6f bar_ref=%.6f diverge=%.2f%% "
                            "> %.2f%% — distrust mid, using bar_ref (guard, not a "
                            "block)",
                            instrument_id, mid, bar_ref, divergence * 100.0,
                            quote_sanity_pct() * 100.0,
                        )
                        return bar_ref
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
        db_writer: DBWriter | None = None,
    ) -> None:
        self._db_path = db_path
        self._flush_interval = flush_interval_sec
        # db-writer-reader-split (opt-in — default None is BYTE-IDENTICAL to the
        # pre-split dedicated-conn path below). When set AND the kill switch
        # reads enabled, the flush submits a job to the shared single-RW-conn
        # writer instead of opening/using ``self._conn``.
        self._db_writer = db_writer
        # Coalesce: last-write-wins per instrument_id (PK collisions dropped).
        self._buf: dict[str, QuoteTick] = {}
        # Process-shared live price view: instrument_id -> (mid, last_ws_monotonic).
        # Consumers read this in-mem (0 DB hits). Updated on every on_quote.
        self._live_px: dict[str, tuple[float, float]] = {}
        # Short per-instrument tick history (newest-last) for G4's tick_window.
        # In-mem only (M2: on_quote never touches the DB); capped at
        # ``RING_BUFFER_DEPTH`` so it stays O(1) per tick and bounded in size.
        self._ring: dict[str, deque[QuoteTick]] = {}
        # Per-venue rolling tick-rate / flow-size accumulator → tick_inflow row
        # (Sentinel S6 source). In-mem, loop-thread only; the flush snapshots a
        # rollup (no await) and UPSERTs it in the SAME txn as the quote rows.
        self._inflow: dict[str, _VenueInflow] = {}
        # Per-SYMBOL rolling motion accumulator (#39) — tick rate / short move /
        # intraday range the candidate sweep reads to surface ACTIVE majors over
        # calm wide-daily names. In-mem, loop-thread only; NEVER persisted (the
        # flush writes quote rows + per-venue tick_inflow only). Keyed on
        # instrument_id (cross-venue safe) and LRU-capped at ACTIVATION_MAX_SYMBOLS
        # (no infinite growth — retention guard).
        self._activation: dict[str, _SymbolActivation] = {}
        # Dedicated WS-writer connection (opened lazily on first flush, in the
        # executor thread — sqlite objects are thread-affine).
        self._conn: sqlite3.Connection | None = None
        self.flush_count = 0
        self.rows_written = 0
        # WAL-backpressure drop accounting (rate-limited WARNING, see
        # ``_LOCK_WARN_INTERVAL_SEC``) — kept distinct from genuine flush faults
        # (which stay ERROR+traceback) so a real bug never hides behind the
        # expected per-second lock drops.
        self.lock_drops = 0
        self.lock_ticks_dropped = 0
        # Quote sanity guard counter (P2a) — bumped by ``live_or_bar_price`` each
        # time a fresh mid is distrusted for diverging from a real bar_ref.
        self.quote_sanity_rejects = 0
        # -inf sentinel = "never warned" → the FIRST backpressure drop always emits
        # its summary immediately (this WARNING is the signal that replaces the old
        # per-drop ERROR storm, so it must not be delayed by a low monotonic clock).
        self._last_lock_warn = float("-inf")

    # ------------------------------------------------------------------
    # In-mem path (WS recv callback) — M2: NO DB access here.
    # ------------------------------------------------------------------

    def on_quote(self, tick: QuoteTick) -> None:
        """Record one tick in memory (coalesce + live_px + ring + inflow). Never awaits."""
        self._buf[tick.instrument_id] = tick
        self._live_px[tick.instrument_id] = (tick.mid, time.monotonic())
        ring = self._ring.get(tick.instrument_id)
        if ring is None:
            ring = deque(maxlen=RING_BUFFER_DEPTH)
            self._ring[tick.instrument_id] = ring
        ring.append(tick)
        # Per-venue inflow accumulator (tick rate + flow-size death for S6). Keyed
        # on the venue ts (epoch seconds) so it aligns with Sentinel's now_ts.
        acc = self._inflow.get(tick.venue)
        if acc is None:
            acc = _VenueInflow()
            self._inflow[tick.venue] = acc
        acc.add(tick.ts, tick.bid_size + tick.ask_size)
        # Per-symbol motion accumulator (#39). Keyed on instrument_id (cross-venue
        # safe); only mids > 0 carry motion (a 0/negative mid is degenerate).
        if tick.mid > 0.0:
            sym = self._activation.get(tick.instrument_id)
            if sym is None:
                sym = self._activation_seat(tick.instrument_id)
            sym.add(tick.ts, tick.mid)

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
        # Snapshot the deque atomically before iterating (STALL fix #74): the
        # Layer-0 focus refresh now reads this from a worker thread (offloaded
        # ``build_technical_lean``) while WS callbacks ``append`` on the loop thread.
        # ``list(deque)`` is a single C-level copy (atomic under the GIL), so the
        # comprehension below can never hit "deque mutated during iteration".
        snapshot = list(ring)
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
            for t in snapshot
        ]

    def activation_metrics(self, instrument_id: str) -> dict[str, float | int] | None:
        """Per-symbol motion snapshot for the candidate sweep, or None (#39).

        Returns ``{ticks_600s, mid_120s_ago, mid_high_600s, mid_low_600s,
        last_mid, last_ts}`` over the trailing 600s window (stale buckets pruned)
        — the per-symbol tick rate / short move / intraday range the sweep's
        activation blend reads. None when no WS tick has been seen for the
        instrument yet (the sweep then degrades that name's live-motion components
        to neutral, never an error — flow_not_block). 0 DB hits.
        """
        sym = self._activation.get(instrument_id)
        if sym is None:
            return None
        return sym.snapshot()

    def _activation_seat(self, instrument_id: str) -> _SymbolActivation:
        """Seat a new per-symbol accumulator, LRU-evicting the oldest if at cap.

        The map is bounded at ``ACTIVATION_MAX_SYMBOLS`` — when seating would
        overflow it, the instrument with the OLDEST ``last_tick_ts`` is evicted
        first (a name nobody has ticked in longest). This is the retention guard:
        the in-mem state can NEVER grow without bound over a long run.
        """
        if len(self._activation) >= ACTIVATION_MAX_SYMBOLS:
            oldest = min(
                self._activation, key=lambda k: self._activation[k].last_tick_ts
            )
            del self._activation[oldest]
        acc = _SymbolActivation()
        self._activation[instrument_id] = acc
        return acc

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
        # clear() no callback can interleave (single-threaded loop). The inflow
        # rollup is snapshotted in the SAME no-await window so the tick_inflow row
        # is consistent with the quote rows it ships alongside.
        if not self._buf:
            return
        snapshot = list(self._buf.values())
        self._buf.clear()
        inflow_rows = [
            (venue, *acc.rollup()) for venue, acc in self._inflow.items()
        ]
        # db-writer-reader-split (opt-in): submit is a cheap non-blocking queue
        # put (no executor hop needed) — the shared writer thread owns the
        # single RW conn + transaction boundary. Fire-and-forget, matching the
        # pre-split "a dropped batch just defers to the next flush" contract
        # (DBWriter counts a drop/failure itself; there is no per-writer WAL
        # lock left to contend since only ONE RW conn exists process-wide).
        if self._db_writer is not None and dbwriter_enabled():
            self._db_writer.submit(
                lambda conn: self._write_rows(conn, snapshot, inflow_rows),
                label="quote_writer",
            )
            return
        try:
            await loop.run_in_executor(
                None, self._flush_blocking, snapshot, inflow_rows
            )
        except sqlite3.OperationalError as exc:  # noqa: BLE001 — write must never halt the bot (AGGRESSIVE)
            msg = str(exc).lower()
            if "lock" in msg or "busy" in msg:
                # EXPECTED WAL backpressure — count it and summarise at most once
                # per interval (never a per-second ERROR+traceback storm).
                self.lock_drops += 1
                self.lock_ticks_dropped += len(snapshot)
                now = time.monotonic()
                if now - self._last_lock_warn >= _LOCK_WARN_INTERVAL_SEC:
                    logger.warning(
                        "[quote_writer] WAL backpressure: %d flush drops (%d ticks) "
                        "so far — latest prices refresh on the next flush",
                        self.lock_drops, self.lock_ticks_dropped,
                    )
                    self._last_lock_warn = now
            else:
                logger.exception(
                    "[quote_writer] flush of %d ticks failed — dropping batch", len(snapshot)
                )
        except Exception:  # noqa: BLE001 — write must never halt the bot (AGGRESSIVE)
            logger.exception(
                "[quote_writer] flush of %d ticks failed — dropping batch", len(snapshot)
            )

    def _write_rows(
        self,
        conn: sqlite3.Connection,
        ticks: list[QuoteTick],
        inflow_rows: list[tuple[str, int, int, float, int]],
    ) -> None:
        """Statement body shared by the legacy dedicated-conn flush
        (``_flush_blocking``, wraps this in its own BEGIN/COMMIT) and the
        db-writer-reader-split job path (the shared ``DBWriter`` wraps this in
        its own per-job SAVEPOINT inside its batch transaction). Issues NO
        BEGIN/COMMIT/SAVEPOINT of its own — the caller owns the txn boundary.
        """
        rows = [_row(q) for q in ticks]
        conn.executemany(_INSERT_SQL, rows)
        if inflow_rows:
            conn.executemany(_INFLOW_UPSERT_SQL, inflow_rows)
        self.flush_count += 1
        self.rows_written += len(rows)

    def _flush_blocking(
        self,
        ticks: list[QuoteTick],
        inflow_rows: list[tuple[str, int, int, float, int]],
    ) -> None:
        """Blocking sqlite write — runs in the executor thread, never the loop.

        Wraps the quote batch AND the per-venue tick_inflow UPSERT in ONE explicit
        transaction so the autocommit conn does ONE WAL commit for the whole batch
        (M1a, not one per row) and the inflow row never skews from the quotes it
        ships with (skew 0 — same BEGIN..COMMIT).
        """
        conn = self._ensure_conn()
        conn.execute("BEGIN")
        try:
            self._write_rows(conn, ticks, inflow_rows)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

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
