"""Per-exchange VIRTUAL account equity — continuously compounding (Jin 2026-07-07).

Derives "current account equity" for one exchange from the internal fills
ledger (sim fills only — zero venue calls), anchored at its $100k seed UNLESS
a ``virtual_ruin_events`` re-seed has fired for that exchange, in which case
the anchor becomes the LATEST ruin's ``reseeded_to`` value at its
``ruined_ts`` (mirrors the ``measurement_resets`` forward-window pattern
already used for the dashboard's since-reset rollup — same idea, scoped
per-exchange). This keeps the account CONTINUOUSLY COMPOUNDING (no periodic
reset) while still being able to "re-seed" cleanly after a ruin event.

Realized PnL is NET of fees, excludes RECONCILED (tracking-failure) fills —
same convention as ``snapshot_q_equity._realised_pnl_since``. Unrealized PnL
is read from open ``positions`` marks (best-effort; 0.0 when absent).
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple


class VirtualEquity(NamedTuple):
    """One exchange's derived virtual equity at ``now``."""

    exchange: str
    seed_anchor: float  # $100k, or the latest ruin's reseeded_to
    anchor_ts: int  # 0 (epoch) unless a ruin re-anchored this exchange
    realized_pnl_usd: float  # net-of-fees, since anchor_ts
    unrealized_pnl_usd: float
    equity: float  # seed_anchor + realized_pnl_usd + unrealized_pnl_usd


def _latest_ruin_anchor(
    conn: sqlite3.Connection, *, exchange: str
) -> tuple[float, int] | None:
    """(reseeded_to, ruined_ts) of the most recent ruin for ``exchange``, else None."""
    try:
        row = conn.execute(
            "SELECT reseeded_to, ruined_ts FROM virtual_ruin_events "
            "WHERE exchange = ? ORDER BY ruined_ts DESC, ruin_id DESC LIMIT 1",
            (exchange,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return float(row[0] or 0.0), int(row[1] or 0)


def _realized_pnl_net_since(
    conn: sqlite3.Connection, *, exchange: str, since_ms: int
) -> float:
    """Net-of-fees realized PnL for ``exchange`` since ``since_ms`` (fills.ts_ms).

    Excludes RECONCILED (tracking-failure) fills — mirrors
    ``snapshot_q_equity._realised_pnl_since``.
    """
    try:
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN f.is_close = 1 THEN f.pnl_usd
                                         ELSE 0.0 END), 0.0)
                      - COALESCE(SUM(f.fee_usd), 0.0)
               FROM fills f
               LEFT JOIN positions p ON p.position_id = f.contribution_id
               WHERE f.venue = ? AND f.ts_ms >= ?
                 AND (p.status IS NULL OR p.status != 'reconciled')""",
            (exchange, since_ms),
        ).fetchone()
    except sqlite3.Error:
        return 0.0
    return float(row[0] or 0.0) if row else 0.0


def _unrealized_pnl_now(conn: sqlite3.Connection, *, exchange: str) -> float:
    """Sum of open positions' unrealized PnL for ``exchange`` (best-effort).

    Reads ``positions.upnl_usd`` when the column carries a live mark (P5 live
    recalc keeps it fresh); missing/absent column degrades to 0.0 — never
    raises, never a trading path.
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
        if "upnl_usd" not in cols:
            return 0.0
        row = conn.execute(
            "SELECT COALESCE(SUM(upnl_usd), 0.0) FROM positions "
            "WHERE venue = ? AND status = 'open'",
            (exchange,),
        ).fetchone()
    except sqlite3.Error:
        return 0.0
    return float(row[0] or 0.0) if row else 0.0


def virtual_equity_now(
    conn: sqlite3.Connection, *, exchange: str, seed_equity: float
) -> VirtualEquity:
    """Derive ``exchange``'s current virtual equity from the internal ledger.

    Anchored at ``seed_equity`` from epoch (ts=0) UNLESS a ruin re-seed
    exists for this exchange, in which case the anchor becomes that ruin's
    ``reseeded_to`` at its ``ruined_ts`` — realized PnL is only summed
    FORWARD of the anchor, so a pre-ruin blowup is never double-counted
    after the re-seed.
    """
    anchor_ruin = _latest_ruin_anchor(conn, exchange=exchange)
    anchor_value = seed_equity if anchor_ruin is None else anchor_ruin[0]
    anchor_ts = 0 if anchor_ruin is None else anchor_ruin[1]
    realized = _realized_pnl_net_since(
        conn, exchange=exchange, since_ms=anchor_ts * 1000,
    )
    unrealized = _unrealized_pnl_now(conn, exchange=exchange)
    return VirtualEquity(
        exchange=exchange,
        seed_anchor=anchor_value,
        anchor_ts=anchor_ts,
        realized_pnl_usd=realized,
        unrealized_pnl_usd=unrealized,
        equity=anchor_value + realized + unrealized,
    )
