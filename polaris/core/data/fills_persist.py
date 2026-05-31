"""Layer 1 — Fill persistence (write + query helpers for the ``fills`` table).

Spec source:
- vault/10_decisions/ADR-003-8-layer-architecture.md (Unified SQLite Schema)
- vault/30_components/layer-1-canonical-baseline.md (Fill section)

The unified ``Fill`` dataclass (``polaris.core.data.fill_normalizer``) is the
single carrier crossing the venue boundary. This module owns the boring
SQLite I/O so the smoke loop / pipeline / dashboard can share one
implementation.

Idempotency
-----------
``persist_fill`` uses ``INSERT OR REPLACE`` keyed on ``fill_id``. Caller
provides a stable ``fill_id`` (we default to ``f"{venue}:{order_id}:open"``
or ``...:close`` so the same venue order does not double-write).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from polaris.core.data.fill_normalizer import Fill

__all__ = [
    "make_fill_id",
    "persist_fill",
    "read_recent_fills",
]


def make_fill_id(fill: Fill, *, is_close: bool) -> str:
    """Stable id for idempotent write — ``{venue}:{order_id}:{phase}``."""
    phase = "close" if is_close else "open"
    order = fill.order_id or fill.client_order_id or "noorder"
    return f"{fill.venue}:{order}:{phase}"


def persist_fill(
    conn: sqlite3.Connection,
    fill: Fill,
    *,
    is_close: bool = False,
    pnl_usd: float = 0.0,
    contribution_id: str | None = None,
    fill_id: str | None = None,
) -> str:
    """Idempotently insert / update one ``fills`` row. Returns the fill_id.

    ``is_close`` distinguishes entry vs close fills so the dashboard can
    render a side-aware timeline. ``pnl_usd`` is non-zero only for closes
    (caller computes from entry vs exit).
    """
    fid = fill_id or make_fill_id(fill, is_close=is_close)
    conn.execute(
        """
        INSERT OR REPLACE INTO fills (
            fill_id, venue, instrument_id, strategy_id, side,
            size_usd, fill_price, fee_usd, slippage_bps, ts_ms,
            order_id, contribution_id, pnl_usd, is_close,
            base_qty, quote_qty, state
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fid,
            fill.venue,
            fill.instrument_id,
            fill.strategy_id,
            fill.side,
            float(fill.size_usd),
            float(fill.fill_price),
            float(fill.fee_usd),
            float(fill.slippage_bps),
            int(fill.ts_ms),
            fill.order_id or "",
            contribution_id,
            float(pnl_usd),
            1 if is_close else 0,
            float(fill.base_qty),
            float(fill.quote_qty),
            fill.state,
        ),
    )
    return fid


def read_recent_fills(
    conn: sqlite3.Connection,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` fills as dicts (newest first).

    Best-effort: returns an empty list when the table is missing.
    """
    try:
        rows = conn.execute(
            """
            SELECT fill_id, venue, instrument_id, strategy_id, side,
                   size_usd, fill_price, fee_usd, slippage_bps, ts_ms,
                   order_id, contribution_id, pnl_usd, is_close,
                   base_qty, quote_qty, state
            FROM fills
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "fill_id": r[0],
            "venue": r[1],
            "instrument_id": r[2],
            "strategy_id": r[3],
            "side": r[4],
            "size_usd": r[5],
            "fill_price": r[6],
            "fee_usd": r[7],
            "slippage_bps": r[8],
            "ts_ms": r[9],
            "order_id": r[10],
            "contribution_id": r[11],
            "pnl_usd": r[12],
            "is_close": bool(r[13]),
            "base_qty": r[14],
            "quote_qty": r[15],
            "state": r[16],
        }
        for r in rows
    ]
