"""One-time guarded reconcile of unexitable Alpaca dead-feed zombies.

Jin 2026-06-22 (coherence audit): the Alpaca paper feed went 98.7h dead,
leaving ``status='open'`` Alpaca positions (CAST/GPUS/CRVO) that can never be
exited against the dead feed. This module marks those unmanageable opens
TERMINAL so the exit engine + hydrate stop retrying them.

It is the SQL-level sibling of ``_production_close_helpers._reconcile_orphan``
and obeys the SAME Step M reconcile-as-drift-counter contract: a reconcile is a
TRACKING FAILURE, NOT a trade.

- ``status='reconciled'`` + ``exit_state='reconciled'`` (lifecycle terminal).
- ``pnl_r`` LEFT NULL — EXCLUDED from every R aggregation (PF/WR/avg_r). Never
  stamps mae→pnl_r (that earlier honesty stamp INFLATED the R ledger).
- a ``zombie_reconciled`` drift note per position in ``risk_events`` carrying a
  rough DOLLAR drift estimate (``min(0, mae_r) × risk_usd``) — display-only,
  never mixed into the R ledger.
- NO fill is fabricated (no invented close leg / price).

Guarded + idempotent: ONLY Alpaca ``status='open'`` rows are touched; a second
run reconciles 0. Safe to run at the RESET step. flow_not_block: clearing dead
zombies UNblocks the book — it is a data-health recovery, not a throttle.

DEMO/PAPER only.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["reconcile_alpaca_venue_drift", "reconcile_alpaca_zombies"]


def reconcile_alpaca_zombies(
    conn: sqlite3.Connection, *, now_ts: int | None = None
) -> int:
    """Mark every Alpaca ``status='open'`` position terminal (reconciled).

    Returns the number of positions reconciled (0 on a clean / already-reconciled
    book — idempotent). Each reconciled row keeps ``pnl_r`` NULL and gets a
    ``zombie_reconciled`` drift-note row in ``risk_events``. No fills fabricated.
    """
    now = now_ts if now_ts is not None else int(time.time())
    rows = conn.execute(
        "SELECT position_id, symbol, strategy_id, mae_r, risk_usd "
        "FROM positions WHERE venue = 'alpaca' AND status = 'open'"
    ).fetchall()
    if not rows:
        return 0
    reconciled = 0
    for position_id, symbol, strategy_id, mae_r, risk_usd in rows:
        # Rough DOLLAR drift estimate (display-only): worst adverse excursion
        # (mae_r, floored at 0) × the persisted 1R-in-dollars. NULL → 0.0. This
        # is the drift COUNTER (a tracking failure), never stamped into pnl_r.
        est_drift_usd = 0.0
        if mae_r is not None and risk_usd is not None:
            est_drift_usd = min(0.0, float(mae_r)) * float(risk_usd)
        try:
            conn.execute("BEGIN IMMEDIATE")
            # pnl_r stays NULL — excluded from R sums. Only the lifecycle marker
            # is written so the exit engine + hydrate stop retrying the zombie.
            conn.execute(
                "UPDATE positions SET status = 'reconciled', closed_ts = ?, "
                "exit_state = 'reconciled' WHERE position_id = ?",
                (now, position_id),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            logger.error(
                "[zombie-reconcile] alpaca:%s reconcile UPDATE failed "
                "(state preserved): %r",
                symbol, exc,
            )
            continue
        audit = json.dumps(
            {
                "venue": "alpaca",
                "symbol": symbol,
                "position_id": position_id,
                "est_drift_usd": est_drift_usd,
                "reason": "alpaca dead feed (98.7h stale) — unexitable open",
            },
            separators=(",", ":"),
        )
        with contextlib.suppress(sqlite3.Error):
            conn.execute(
                "INSERT INTO risk_events "
                "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
                "VALUES (?, ?, 'zombie_reconciled', ?, ?)",
                (uuid.uuid4().hex, strategy_id, now, audit),
            )
            conn.commit()
        reconciled += 1
        logger.warning(
            "[zombie-reconcile] alpaca:%s position_id=%s reconciled — "
            "status='reconciled', no fill, pnl_r=NULL (tracking failure, "
            "excluded from R ledger), est_drift_usd=%.2f (dead-feed recovery)",
            symbol, position_id, est_drift_usd,
        )
    return reconciled


async def reconcile_alpaca_venue_drift(
    conn: sqlite3.Connection, adapter: Any, *, now_ts: int | None = None,
) -> int:
    """Reconcile internal Alpaca ``open`` rows the VENUE no longer holds.

    Audit1 P0-4 ② (2026-07-02): the inverse drift of ``reconcile_venue_positions``
    (venue holds a position the DB does not track). Here the DB tracks a
    ``status='open'`` Alpaca position (e.g. UPC/ABNB/SLS) but the LIVE venue
    book has NO matching symbol — the position was closed/liquidated externally
    (a demo-side auto-close, a manual close outside the bot, or a prior reset)
    and our internal ledger never learned about it. Unlike
    ``reconcile_alpaca_zombies`` (unconditional — marks EVERY Alpaca open row
    terminal), this CHECKS the live venue book first and reconciles ONLY the
    rows the venue confirms absent — a row with a matching live venue position
    is left untouched (it is a genuine open, not drift).

    Same Step M reconcile-as-drift-counter contract as
    ``_production_close_helpers._reconcile_orphan`` / ``reconcile_alpaca_zombies``:
    ``status='reconciled'`` + ``exit_state='reconciled'``, ``pnl_r`` LEFT NULL
    (excluded from every R aggregation), a ``alpaca_venue_drift`` audit row in
    ``risk_events`` carrying a rough DOLLAR drift estimate
    (``min(0, mae_r) x risk_usd``, display-only). NO fill is fabricated.

    A venue read failure skips the WHOLE pass (fail-safe — never reconciles
    blind on a transport error). Idempotent: only ``status='open'`` rows are
    read, so a second run reconciles 0. DEMO/PAPER only.
    """
    now = now_ts if now_ts is not None else int(time.time())
    try:
        venue_positions = await adapter.fetch_positions()
    except Exception as exc:  # noqa: BLE001 — read failed → skip, never blind
        logger.warning(
            "[venue-drift/alpaca] fetch_positions failed %r — skip pass", exc,
        )
        return 0
    venue_symbols = {
        str(p.get("symbol") or "") for p in (venue_positions or [])
        if p.get("symbol")
    }
    rows = conn.execute(
        "SELECT position_id, symbol, strategy_id, mae_r, risk_usd "
        "FROM positions WHERE venue = 'alpaca' AND status = 'open'"
    ).fetchall()
    drifted = [r for r in rows if str(r[1]) not in venue_symbols]
    if not drifted:
        return 0
    reconciled = 0
    for position_id, symbol, strategy_id, mae_r, risk_usd in drifted:
        est_drift_usd = 0.0
        if mae_r is not None and risk_usd is not None:
            est_drift_usd = min(0.0, float(mae_r)) * float(risk_usd)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE positions SET status = 'reconciled', closed_ts = ?, "
                "exit_state = 'reconciled' WHERE position_id = ?",
                (now, position_id),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            logger.error(
                "[venue-drift/alpaca] %s reconcile UPDATE failed "
                "(state preserved): %r",
                symbol, exc,
            )
            continue
        audit = json.dumps(
            {
                "venue": "alpaca",
                "symbol": symbol,
                "position_id": position_id,
                "est_drift_usd": est_drift_usd,
                "reason": "internal open, venue position absent (ledger drift)",
            },
            separators=(",", ":"),
        )
        with contextlib.suppress(sqlite3.Error):
            conn.execute(
                "INSERT INTO risk_events "
                "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
                "VALUES (?, ?, 'alpaca_venue_drift', ?, ?)",
                (uuid.uuid4().hex, strategy_id, now, audit),
            )
            conn.commit()
        reconciled += 1
        logger.warning(
            "[venue-drift/alpaca] %s position_id=%s reconciled — venue has no "
            "matching position (status='reconciled', pnl_r=NULL, tracking "
            "failure not a trade), est_drift_usd=%.2f",
            symbol, position_id, est_drift_usd,
        )
    return reconciled
