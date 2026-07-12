"""Weekly per-exchange equity/PnL trace — non-destructive (Jin 2026-07-07).

TRACE != RESET: the virtual account compounds CONTINUOUSLY across week
boundaries; this module only upserts a Monday-anchored (UTC) row per exchange
marking "this week so far" (realized + unrealized PnL, trade count, current
equity). Crossing Monday starts a NEW row keyed on the new ``week_start_ts`` —
the running account balance is never touched here.

OBSERVABILITY-ONLY: never read by sizing/gating/exit; a missing row degrades
to the caller's own zero-default (``upsert_weekly_row`` always creates one).

SINGLE-OWNER FRESH-SUM (ledger-reconcile forensic 2026-07-12, bug②): earlier
``realized_pnl_usd``/``trades`` were a read-modify-write accumulator — a
fail-open exception mid-``upsert_weekly_row`` silently dropped that close's
delta forever (11/556 close fills lost, -$494.78 unrecorded in one audited
week). Both fields are now FRESH-SUMMED from ``fills`` on every call, mirroring
the CLEAN single-owner definition ``virtual_account_equity._realized_pnl_net_
since`` / ``snapshot_q_equity._realised_pnl_since`` already use (REAL-fee net,
RECONCILED fills excluded) — never an accumulated delta, so a dropped close
self-heals the moment the NEXT close successfully upserts this week's row.
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


def _fresh_week_realized(
    conn: sqlite3.Connection, *, exchange: str, since_ms: int
) -> tuple[float, int]:
    """(realized_pnl_usd, trades) since ``since_ms`` — fresh-SUM from ``fills``.

    Mirrors ``virtual_account_equity._realized_pnl_net_since`` /
    ``snapshot_q_equity._realised_pnl_since`` byte-for-byte (REAL-fee net,
    RECONCILED-status positions excluded, orphan fills kept). Recomputed from
    the fills ledger — the already-committed source of truth — on every call,
    so there is no accumulator state to lose.
    """
    try:
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN f.is_close = 1 THEN f.pnl_usd
                                         ELSE 0.0 END), 0.0)
                      - COALESCE(SUM(f.fee_usd), 0.0),
                      COALESCE(SUM(f.is_close), 0)
               FROM fills f
               LEFT JOIN positions p ON p.position_id = f.contribution_id
               WHERE f.venue = ? AND f.ts_ms >= ?
                 AND (p.status IS NULL OR p.status != 'reconciled')""",
            (exchange, since_ms),
        ).fetchone()
    except sqlite3.Error:
        return 0.0, 0
    if row is None:
        return 0.0, 0
    return float(row[0] or 0.0), int(row[1] or 0)


def upsert_weekly_row(
    conn: sqlite3.Connection,
    *,
    exchange: str,
    now_ts: int,
    account_equity: float,
    unrealized_pnl_usd: float | None = None,
) -> WeeklyEquityRow:
    """Upsert this week's trace row for ``exchange`` — NEVER resets the account.

    Creates the row on first touch of a new Monday-anchored week with
    ``start_equity = account_equity`` (this week's opening mark, carried over
    from whatever the continuously-compounding account already was — trace
    != reset). ``realized_pnl_usd``/``trades`` are FRESH-SUMMED from ``fills``
    for [week_start, now_ts] on EVERY call (single-owner — see module
    docstring), not accumulated, so a fail-open-dropped close self-heals on
    the next successful call. ``current_equity`` / ``unrealized_pnl_usd``
    overwrite with the latest snapshot (``unrealized_pnl_usd=None`` leaves the
    stored value unchanged — for callers that don't recompute uPnL).
    """
    week = week_start_ts(now_ts)
    existing = get_week_row(conn, exchange=exchange, week_start=week)
    realized, trades = _fresh_week_realized(
        conn, exchange=exchange, since_ms=week * 1000
    )
    if existing is None:
        new_unrealized = unrealized_pnl_usd if unrealized_pnl_usd is not None else 0.0
        conn.execute(
            "INSERT INTO weekly_equity_curve "
            "(exchange, week_start_ts, start_equity, current_equity, "
            " realized_pnl_usd, unrealized_pnl_usd, trades, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                exchange, week, account_equity, account_equity,
                realized, new_unrealized, trades, now_ts,
            ),
        )
    else:
        new_unrealized = (
            unrealized_pnl_usd if unrealized_pnl_usd is not None
            else existing.unrealized_pnl_usd
        )
        conn.execute(
            "UPDATE weekly_equity_curve SET current_equity = ?, "
            "realized_pnl_usd = ?, unrealized_pnl_usd = ?, trades = ?, "
            "updated_ts = ? WHERE exchange = ? AND week_start_ts = ?",
            (
                account_equity, realized, new_unrealized, trades,
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
