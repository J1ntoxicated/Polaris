"""Ticker baseline computation — 5 metrics × rolling window → p50/p75 state.

Pure-function stats helpers + thin SQLite glue (read/write).

Spec source: vault/30_components/layer-1-canonical-baseline.md (Q2 + Q3 + Q5).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence

logger = logging.getLogger(__name__)

from polaris.core.data.schema import (
    ALLOWED_METRICS,
    LOOKBACK_FAST_SEC,
    LOOKBACK_SLOW_SEC,
    BaselineValue,
)

# ---------------------------------------------------------------------------
# Pure stats
# ---------------------------------------------------------------------------


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile on a pre-sorted sequence (pct in [0,1])."""
    if not sorted_values:
        raise ValueError("empty sample sequence")
    if pct <= 0.0:
        return float(sorted_values[0])
    if pct >= 1.0:
        return float(sorted_values[-1])
    n = len(sorted_values)
    pos = pct * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_values[lo]) * (1.0 - frac) + float(sorted_values[hi]) * frac


def compute_baseline(
    *,
    metric: str,
    samples: Iterable[float],
    updated_ts: int,
    lookback_sec: int | None = None,
) -> BaselineValue:
    """Build a `BaselineValue` from raw samples (rolling-window already filtered).

    Stats invariant: ``p25 <= p50 <= p75`` for any sample population (property test).
    """
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"unknown metric {metric!r}; allowed: {sorted(ALLOWED_METRICS)}")
    arr = sorted(float(s) for s in samples)
    if not arr:
        raise ValueError("compute_baseline: zero samples")
    p50 = _percentile(arr, 0.50)
    p75 = _percentile(arr, 0.75)
    # Sanity: tied populations may collapse p50==p75.
    if p75 < p50:
        p75 = p50
    lookback = lookback_sec if lookback_sec is not None else _default_lookback(metric)
    return BaselineValue(
        metric=metric,
        p50=p50,
        p75=p75,
        sample_count=len(arr),
        lookback_sec=lookback,
        updated_ts=updated_ts,
    )


def _default_lookback(metric: str) -> int:
    return LOOKBACK_SLOW_SEC if metric == "pnl_std" else LOOKBACK_FAST_SEC


# ---------------------------------------------------------------------------
# SQLite I/O glue (impure, but thin)
# ---------------------------------------------------------------------------


def upsert_baseline_state(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
    underlying_group_id: str,
    baseline: BaselineValue,
    asset_class: str = "",
) -> None:
    """Idempotently write `ticker_baseline_state` row."""
    conn.execute(
        """
        INSERT INTO ticker_baseline_state
            (instrument_id, underlying_group_id, asset_class, metric,
             baseline_p50, baseline_p75, sample_count, lookback_sec, updated_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instrument_id, metric) DO UPDATE SET
            underlying_group_id=excluded.underlying_group_id,
            asset_class=excluded.asset_class,
            baseline_p50=excluded.baseline_p50,
            baseline_p75=excluded.baseline_p75,
            sample_count=excluded.sample_count,
            lookback_sec=excluded.lookback_sec,
            updated_ts=excluded.updated_ts
        """,
        (
            instrument_id,
            underlying_group_id,
            asset_class,
            baseline.metric,
            baseline.p50,
            baseline.p75,
            baseline.sample_count,
            baseline.lookback_sec,
            baseline.updated_ts,
        ),
    )


def read_baseline_state(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
    metric: str,
) -> BaselineValue | None:
    """Read latest baseline row for (instrument_id, metric); None if absent."""
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    row = conn.execute(
        """
        SELECT metric, baseline_p50, baseline_p75, sample_count, lookback_sec, updated_ts
        FROM ticker_baseline_state
        WHERE instrument_id = ? AND metric = ?
        """,
        (instrument_id, metric),
    ).fetchone()
    if row is None:
        return None
    return BaselineValue(
        metric=str(row[0]),
        p50=float(row[1]),
        p75=float(row[2]),
        sample_count=int(row[3]),
        lookback_sec=int(row[4]),
        updated_ts=int(row[5]),
    )


def append_sample(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
    underlying_group_id: str,
    metric: str,
    ts: int,
    value: float,
) -> None:
    """Append a raw sample to `ticker_baseline_samples` (idempotent on (id,metric,ts))."""
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    conn.execute(
        """
        INSERT OR REPLACE INTO ticker_baseline_samples
            (instrument_id, underlying_group_id, metric, ts, value)
        VALUES (?, ?, ?, ?, ?)
        """,
        (instrument_id, underlying_group_id, metric, ts, float(value)),
    )


def append_samples_batch(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[str, str, str, int, float]],
) -> int:
    """Bulk twin of ``append_sample`` — one ``executemany`` for many rows.

    Each row is ``(instrument_id, underlying_group_id, metric, ts, value)``.
    Same INSERT OR REPLACE statement/table/idempotency contract as
    ``append_sample`` (upsert on (instrument_id, metric, ts)) — only the
    write API is batched (a row-by-row ``execute`` loop -> one
    ``executemany`` call), which cuts the per-statement Python/C round-trip
    overhead a large ingest batch pays while holding the caller's (DBWriter
    SAVEPOINT / batch) transaction open. No transaction control here — same
    as ``append_sample``, whatever the caller already opened stays as-is.
    Returns the row count submitted; empty input is a no-op (0 calls).
    """
    rows_list = list(rows)
    if not rows_list:
        return 0
    for _iid, _gid, metric, _ts, _val in rows_list:
        if metric not in ALLOWED_METRICS:
            raise ValueError(f"unknown metric {metric!r}")
    conn.executemany(
        """
        INSERT OR REPLACE INTO ticker_baseline_samples
            (instrument_id, underlying_group_id, metric, ts, value)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(iid, gid, metric, ts, float(val)) for iid, gid, metric, ts, val in rows_list],
    )
    return len(rows_list)


def read_samples_window(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
    metric: str,
    window_start_ts: int,
) -> list[float]:
    """Return sample values within ``[window_start_ts, +inf)``."""
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    cur = conn.execute(
        """
        SELECT value FROM ticker_baseline_samples
        WHERE instrument_id = ? AND metric = ? AND ts >= ?
        ORDER BY ts ASC
        """,
        (instrument_id, metric, window_start_ts),
    )
    return [float(r[0]) for r in cur.fetchall()]


def update_baseline_from_window(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
    underlying_group_id: str,
    metric: str,
    now_ts: int,
    asset_class: str = "",
) -> BaselineValue | None:
    """Recompute `BaselineValue` from current rolling window and persist it.

    Returns the new state, or None if no samples available.
    """
    lookback = _default_lookback(metric)
    samples = read_samples_window(
        conn,
        instrument_id=instrument_id,
        metric=metric,
        window_start_ts=now_ts - lookback,
    )
    if not samples:
        return None
    bv = compute_baseline(metric=metric, samples=samples, updated_ts=now_ts, lookback_sec=lookback)
    upsert_baseline_state(
        conn,
        instrument_id=instrument_id,
        underlying_group_id=underlying_group_id,
        asset_class=asset_class,
        baseline=bv,
    )
    logger.debug(
        "[baseline] %s metric=%s n=%d p50=%.4f p75=%.4f lookback=%ds",
        instrument_id,
        metric,
        bv.sample_count,
        bv.p50,
        bv.p75,
        bv.lookback_sec,
    )
    return bv
