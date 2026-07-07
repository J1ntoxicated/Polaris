"""Weekly per-exchange equity/PnL trace — non-destructive (Jin 2026-07-07).

TRACE != RESET: the virtual account compounds CONTINUOUSLY across week
boundaries; this module only upserts a Monday-anchored (UTC) row per exchange
marking "this week so far" (realized + unrealized PnL, trade count, current
equity). Crossing Monday starts a NEW row keyed on the new ``week_start_ts`` —
the running account balance is never touched here.

OBSERVABILITY-ONLY: never read by sizing/gating/exit; a missing row degrades
to the caller's own zero-default (``upsert_weekly_row`` always creates one).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

SECONDS_PER_DAY: int = 86_400


class WeeklyEquityRow(NamedTuple):
    """One (exchange, week) trace row — display/measurement only."""

    exchange: str
    week_start_ts: int
    start_equity: float
    current_equity: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    trades: int
    updated_ts: int


def week_start_ts(ts: int) -> int:
    """Monday 00:00 UTC of the week containing ``ts`` (unix seconds).

    Monday-anchored per Jin's spec (ISO weekday: Monday=0). Always returns a
    ts at/before ``ts`` — the current week's anchor, never a future one.
    """
    dt = datetime.fromtimestamp(ts, tz=UTC)
    monday = (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(monday.timestamp())


def get_week_row(
    conn: sqlite3.Connection, *, exchange: str, week_start: int
) -> WeeklyEquityRow | None:
    """Read the (exchange, week_start) row, or ``None`` if not yet created."""
    try:
        row = conn.execute(
            "SELECT exchange, week_start_ts, start_equity, current_equity, "
            "realized_pnl_usd, unrealized_pnl_usd, trades, updated_ts "
            "FROM weekly_equity_curve WHERE exchange = ? AND week_start_ts = ?",
            (exchange, week_start),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return WeeklyEquityRow(
        exchange=str(row[0]),
        week_start_ts=int(row[1]),
        start_equity=float(row[2] or 0.0),
        current_equity=float(row[3] or 0.0),
        realized_pnl_usd=float(row[4] or 0.0),
        unrealized_pnl_usd=float(row[5] or 0.0),
        trades=int(row[6] or 0),
        updated_ts=int(row[7] or 0),
    )


def upsert_weekly_row(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    now_ts: int,
    account_equity: float,
    realized_pnl_delta_usd: float = 0.0,
    unrealized_pnl_usd: float | None = None,
    trade_delta: int = 0,
) -> WeeklyEquityRow:
    """Upsert this week's trace row for ``exchange`` — NEVER resets the account.

    Creates the row on first touch of a new Monday-anchored week with
    ``start_equity = account_equity`` (this week's opening mark, carried over
    from whatever the continuously-compounding account already was — trace
    != reset). Subsequent calls in the SAME week accumulate
    ``realized_pnl_delta_usd`` and ``trade_delta`` onto the existing row and
    overwrite ``current_equity`` / ``unrealized_pnl_usd`` with the latest
    snapshot (``unrealized_pnl_usd=None`` leaves the stored value unchanged —
    for realized-only close-path calls that don't recompute uPnL).
    """
    week = week_start_ts(now_ts)
    existing = get_week_row(conn, exchange=exchange, week_start=week)
    if existing is None:
        new_realized = realized_pnl_delta_usd
        new_unrealized = unrealized_pnl_usd if unrealized_pnl_usd is not None else 0.0
        new_trades = max(0, trade_delta)
        conn.execute(
            "INSERT INTO weekly_equity_curve "
            "(exchange, week_start_ts, start_equity, current_equity, "
            " realized_pnl_usd, unrealized_pnl_usd, trades, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                exchange, week, account_equity, account_equity,
                new_realized, new_unrealized, new_trades, now_ts,
            ),
        )
    else:
        new_realized = existing.realized_pnl_usd + realized_pnl_delta_usd
        new_unrealized = (
            unrealized_pnl_usd if unrealized_pnl_usd is not None
            else existing.unrealized_pnl_usd
        )
        new_trades = existing.trades + max(0, trade_delta)
        conn.execute(
            "UPDATE weekly_equity_curve SET current_equity = ?, "
            "realized_pnl_usd = ?, unrealized_pnl_usd = ?, trades = ?, "
            "updated_ts = ? WHERE exchange = ? AND week_start_ts = ?",
            (
                account_equity, new_realized, new_unrealized, new_trades,
                now_ts, exchange, week,
            ),
        )
    row = get_week_row(conn, exchange=exchange, week_start=week)
    assert row is not None  # just written above
    return row


def all_current_week_rows(
    conn: sqlite3.Connection, *, now_ts: int
) -> list[WeeklyEquityRow]:
    """Every exchange's row for the CURRENT week (dashboard/digest surface)."""
    week = week_start_ts(now_ts)
    try:
        rows = conn.execute(
            "SELECT exchange, week_start_ts, start_equity, current_equity, "
            "realized_pnl_usd, unrealized_pnl_usd, trades, updated_ts "
            "FROM weekly_equity_curve WHERE week_start_ts = ? ORDER BY exchange",
            (week,),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [
        WeeklyEquityRow(
            exchange=str(r[0]), week_start_ts=int(r[1]),
            start_equity=float(r[2] or 0.0), current_equity=float(r[3] or 0.0),
            realized_pnl_usd=float(r[4] or 0.0),
            unrealized_pnl_usd=float(r[5] or 0.0),
            trades=int(r[6] or 0), updated_ts=int(r[7] or 0),
        )
        for r in rows
    ]
