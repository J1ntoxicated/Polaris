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

import logging
import sqlite3
from collections.abc import Iterable

from polaris.core.data.baseline import append_sample, update_baseline_from_window
from polaris.core.data.schema import Bar

logger = logging.getLogger(__name__)

__all__ = [
    "BAR_BASELINE_METRICS",
    "ingest_bars",
    "persist_bars",
    "update_baseline_from_bars",
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
