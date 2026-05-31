"""T11 ratio-to-baseline normalize() API — `raw / baseline_p50`.

- Cold-start fallback chain: instrument_id → underlying_group_id → asset_class → 1.0 neutral.
- Output range: ``[0, +inf)`` where 1.0 = baseline.
- z-score / minmax / percentile rejected (Q3 of L1 spec) — ratio preserves
  expansion-signal magnitude (aggressive bias).

Spec source: vault/30_components/layer-1-canonical-baseline.md (Q3 + Q4).
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from polaris.core.data.schema import (
    ALLOWED_METRICS,
    COLD_START_NEUTRAL,
    BaselineValue,
)

__all__ = ["get_baseline", "normalize", "ratio_to_baseline"]


# ---------------------------------------------------------------------------
# Pure ratio
# ---------------------------------------------------------------------------


def ratio_to_baseline(raw_value: float, baseline_p50: float) -> float:
    """Pure ratio function with safety guards.

    Domain: ``[0, +inf)``.

    - Non-finite ``raw_value`` → ``COLD_START_NEUTRAL``.
    - ``raw_value < 0`` → ``COLD_START_NEUTRAL`` (5 baseline metrics
      atr/size/signal/volume/pnl_std are all magnitude-based; signed input
      is ill-defined and would violate output domain).
    - ``baseline_p50 <= 0`` or non-finite → ``COLD_START_NEUTRAL`` (div-by-zero
      guard; cold-start neutral preserves aggressive bias).
    """
    if not math.isfinite(raw_value):
        return COLD_START_NEUTRAL
    if raw_value < 0.0:
        return COLD_START_NEUTRAL
    if baseline_p50 <= 0.0 or not math.isfinite(baseline_p50):
        return COLD_START_NEUTRAL
    return float(raw_value) / float(baseline_p50)


# ---------------------------------------------------------------------------
# DB-backed lookup (cold-start chain)
# ---------------------------------------------------------------------------


def get_baseline(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
    metric: str,
    underlying_group_id: str | None = None,
    asset_class: str | None = None,
    asof_ts: int | None = None,
) -> BaselineValue | None:
    """Resolve baseline via fallback chain (Q4 of L1 spec).

    1. exact (instrument_id, metric)
    2. any sibling instrument under the same underlying_group_id
    3. any sibling instrument under the same asset_class
    4. None → caller falls back to ``COLD_START_NEUTRAL``

    ``asof_ts`` filters baseline rows to those whose ``updated_ts <= asof_ts``;
    None = latest (typical online path).
    """
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"unknown metric {metric!r}")

    asof_clause, asof_args = _asof_clause(asof_ts)

    row = conn.execute(
        f"""
        SELECT metric, baseline_p50, baseline_p75, sample_count, lookback_sec, updated_ts
        FROM ticker_baseline_state
        WHERE instrument_id = ? AND metric = ?{asof_clause}
        """,
        (instrument_id, metric, *asof_args),
    ).fetchone()
    if row is not None:
        return _row_to_baseline(row)

    if underlying_group_id:
        row = conn.execute(
            f"""
            SELECT metric, baseline_p50, baseline_p75, sample_count, lookback_sec, updated_ts
            FROM ticker_baseline_state
            WHERE underlying_group_id = ? AND metric = ?{asof_clause}
            ORDER BY sample_count DESC, updated_ts DESC
            LIMIT 1
            """,
            (underlying_group_id, metric, *asof_args),
        ).fetchone()
        if row is not None:
            return _row_to_baseline(row)

    if asset_class:
        row = conn.execute(
            f"""
            SELECT metric, baseline_p50, baseline_p75, sample_count, lookback_sec, updated_ts
            FROM ticker_baseline_state
            WHERE asset_class = ? AND metric = ?{asof_clause}
            ORDER BY sample_count DESC, updated_ts DESC
            LIMIT 1
            """,
            (asset_class, metric, *asof_args),
        ).fetchone()
        if row is not None:
            return _row_to_baseline(row)

    return None


def normalize(
    conn: sqlite3.Connection,
    *,
    instrument_id: str,
    metric: str,
    raw_value: float,
    underlying_group_id: str | None = None,
    asset_class: str | None = None,
    asof_ts: int | None = None,
) -> float:
    """T11 ratio normalize with cold-start chain. Always returns finite value.

    Output domain: ``[0, +inf)``. ``1.0 = baseline / >1 = expansion / <1 = compression``.
    """
    bv = get_baseline(
        conn,
        instrument_id=instrument_id,
        metric=metric,
        underlying_group_id=underlying_group_id,
        asset_class=asset_class,
        asof_ts=asof_ts,
    )
    if bv is None:
        return COLD_START_NEUTRAL
    return ratio_to_baseline(raw_value, bv.p50)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _asof_clause(asof_ts: int | None) -> tuple[str, tuple[int, ...]]:
    if asof_ts is None:
        return "", ()
    return " AND updated_ts <= ?", (int(asof_ts),)


def _row_to_baseline(row: tuple[Any, ...]) -> BaselineValue:
    return BaselineValue(
        metric=str(row[0]),
        p50=float(row[1]),
        p75=float(row[2]),
        sample_count=int(row[3]),
        lookback_sec=int(row[4]),
        updated_ts=int(row[5]),
    )
