"""Position strategy-segment lineage (P3 self-evolve read-model).

Records the ticker ↔ strategy ↔ exit lineage of every position into
``position_strategy_segments`` so a future self-evolve loop (and the
dashboard) can learn from *what was traded, under which regime, by which
strategy, and how it exited* — without ever touching the live trading path.

Behaviour-0 contract
---------------------
This module ONLY appends/updates lineage rows and reads them back. It does
NOT participate in any sizing / gating / exit / entry decision. Live trading
never reads ``position_strategy_segments``; every public helper here is either
an ``INSERT`` (open / swap), an ``UPDATE`` (close), or a read-only ``SELECT``.

Schema note
-----------
The table predates this module (Layer 6 swap attribution). The lineage columns
``trade_id / venue / ticker / pnl_usd / cell_key`` were added additively
(``schema._apply_post_migrations``). The legacy ``regime_at_start /
started_ts / ended_ts`` columns carry the spec's ``regime / entry_ts /
exit_ts`` meanings — this module maps between the two so callers speak the
lineage vocabulary.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

# entry_reason marker for a lineage-opened segment (distinct from the swap
# path's 'entry'/'swap' markers so the two writers never collide on intent).
ENTRY_REASON_OPEN: Final[str] = "entry"


def build_cell_key(*, venue: str, strategy_id: str, ticker: str, regime: str) -> str:
    """Canonical lineage cell key — ``venue:strategy:ticker:regime``.

    Mirrors the Layer 4 ``CellKeyP0`` dimensions (exchange × strategy × ticker
    × regime) as a flat string so lineage rows join back to the cell matrix.
    """
    return f"{venue}:{strategy_id}:{ticker}:{regime}"


@dataclass(frozen=True, slots=True)
class LineageSegment:
    """One read-model lineage row (open or closed)."""

    segment_id: str
    position_id: str
    trade_id: str
    venue: str
    ticker: str
    strategy_id: str
    regime: str
    entry_ts: int
    exit_ts: int | None
    exit_reason: str | None
    pnl_r: float
    pnl_usd: float
    cell_key: str


def record_segment_open(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    trade_id: str,
    venue: str,
    ticker: str,
    strategy_id: str,
    regime: str,
    entry_ts: int,
) -> str:
    """INSERT an open lineage segment (``exit_ts`` NULL). Returns segment_id.

    Called from the position-open success path. Fail-open: any sqlite error is
    swallowed (the lineage row is a read-model — losing one must never abort or
    alter an already-committed open). ``cell_key`` is derived from the four
    lineage dimensions.
    """
    segment_id = uuid.uuid4().hex
    cell_key = build_cell_key(
        venue=venue, strategy_id=strategy_id, ticker=ticker, regime=regime
    )
    try:
        conn.execute(
            "INSERT INTO position_strategy_segments "
            "(position_id, segment_id, strategy_id, regime_at_start, started_ts, "
            " ended_ts, entry_reason, exit_reason, attribution_weight, pnl_r, "
            " trade_id, venue, ticker, pnl_usd, cell_key) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, 0.0, 0.0, ?, ?, ?, 0.0, ?)",
            (
                position_id,
                segment_id,
                strategy_id,
                regime,
                int(entry_ts),
                ENTRY_REASON_OPEN,
                trade_id,
                venue,
                ticker,
                cell_key,
            ),
        )
    except sqlite3.Error as exc:  # fail-open — read-model write must not abort
        logger.warning(
            "[lineage] open segment record failed pos=%s %s:%s: %r",
            position_id, venue, ticker, exc,
        )
    return segment_id


def record_segment_close(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    exit_ts: int,
    exit_reason: str,
    pnl_r: float,
    pnl_usd: float,
) -> None:
    """UPDATE the open lineage segment(s) for a position at close.

    Stamps ``ended_ts`` (exit_ts) + ``exit_reason`` + realised ``pnl_r`` /
    ``pnl_usd`` onto the still-open segment(s) of the position. Idempotent on
    re-close (only rows with ``ended_ts IS NULL`` are touched). Fail-open.
    """
    try:
        conn.execute(
            "UPDATE position_strategy_segments "
            "SET ended_ts = ?, exit_reason = ?, pnl_r = ?, pnl_usd = ? "
            "WHERE position_id = ? AND ended_ts IS NULL",
            (
                int(exit_ts),
                exit_reason,
                float(pnl_r),
                float(pnl_usd),
                position_id,
            ),
        )
    except sqlite3.Error as exc:  # fail-open — read-model write must not abort
        logger.warning(
            "[lineage] close segment record failed pos=%s: %r", position_id, exc
        )


# ---------------------------------------------------------------------------
# Read-only query helpers (self-evolve learning / dashboard)
# ---------------------------------------------------------------------------

_SELECT_COLS: Final[str] = (
    "segment_id, position_id, trade_id, venue, ticker, strategy_id, "
    "regime_at_start, started_ts, ended_ts, exit_reason, pnl_r, pnl_usd, cell_key"
)


def _row_to_segment(row: tuple[Any, ...]) -> LineageSegment:
    return LineageSegment(
        segment_id=str(row[0]),
        position_id=str(row[1]),
        trade_id=str(row[2] or ""),
        venue=str(row[3] or ""),
        ticker=str(row[4] or ""),
        strategy_id=str(row[5] or ""),
        regime=str(row[6] or ""),
        entry_ts=int(row[7] or 0),
        exit_ts=None if row[8] is None else int(row[8]),
        exit_reason=None if row[9] is None else str(row[9]),
        pnl_r=float(row[10] or 0.0),
        pnl_usd=float(row[11] or 0.0),
        cell_key=str(row[12] or ""),
    )


def lineage_for_cell(
    conn: sqlite3.Connection, cell_key: str, *, limit: int = 200
) -> list[LineageSegment]:
    """All lineage segments for one ``cell_key`` (most-recent first). Read-only."""
    rows = conn.execute(
        f"SELECT {_SELECT_COLS} FROM position_strategy_segments "
        "WHERE cell_key = ? ORDER BY started_ts DESC LIMIT ?",
        (cell_key, int(limit)),
    ).fetchall()
    return [_row_to_segment(r) for r in rows]


def recent_lineage(conn: sqlite3.Connection, n: int = 50) -> list[LineageSegment]:
    """The ``n`` most-recently-opened lineage segments (any cell). Read-only."""
    rows = conn.execute(
        f"SELECT {_SELECT_COLS} FROM position_strategy_segments "
        "ORDER BY started_ts DESC LIMIT ?",
        (int(n),),
    ).fetchall()
    return [_row_to_segment(r) for r in rows]


__all__ = [
    "ENTRY_REASON_OPEN",
    "LineageSegment",
    "build_cell_key",
    "lineage_for_cell",
    "record_segment_close",
    "record_segment_open",
    "recent_lineage",
]
