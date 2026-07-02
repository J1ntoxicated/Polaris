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

__all__ = [
    "reconcile_alpaca_qty_drift",
    "reconcile_alpaca_venue_drift",
    "reconcile_alpaca_zombies",
]

# A venue-vs-internal qty gap below this many dollars (at the position's own
# entry price) is left untouched — sub-$5 float/rounding noise, not a real
# fold/clamp-worthy drift.
ALPACA_QTY_DRIFT_DUST_USD: float = 5.0


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


async def reconcile_alpaca_qty_drift(
    conn: sqlite3.Connection, adapter: Any, *, now_ts: int | None = None,
) -> int:
    """Fold/clamp a QTY-level Alpaca drift on symbols BOTH sides still hold.

    ``reconcile_alpaca_venue_drift`` only catches a symbol the venue no longer
    holds at ALL. This is the finer-grained sibling: for every Alpaca symbol
    with a live venue position, ``diff = venue_qty - Σ(internal open qty)``.
    ``|diff| <= dust`` is left untouched. Live evidence (fix rollout, same
    order_id at both sides): GVH 206 vs internal 206-44=162 tracked...
    partial-fill truncation left the internal ledger short of the venue's true
    holding on 6 symbols (~$4,042 untracked notional).

    diff > 0 (venue holds MORE): attribute to the position's OWN entry order
    first — ``fetch_order`` on ``fills.order_id`` for the entry fill; if its
    venue ``filled_qty`` now exceeds the internal ``qty``, FOLD the delta into
    that position's qty/fills/risk_usd (qty-proportional rescale, entry price
    anchor unchanged) and stamp a ``qty_trueup`` audit row. A slice that can't
    be attributed to a tracked position's own order (unknown extra qty) is left
    for the orphan-adopt path (``reconcile_venue_positions`` /
    ``_reconcile_import``) to pick up as its own untracked slice — this
    function never fabricates a position for it.

    diff < 0 (internal OVER-COUNTS the venue): CLAMP the internal qty DOWN to
    the venue-reported total (never invents a fill) + a ``qty_drift_clamp``
    audit row.

    A venue read failure skips the WHOLE pass (fail-safe, mirrors
    ``reconcile_alpaca_venue_drift``). Idempotent: a second run on an
    already-folded/clamped book computes ``diff == 0`` and touches nothing.
    Never blocks a new entry (flow_not_block) — pure post-hoc ledger honesty.
    DEMO/PAPER only.
    """
    now = now_ts if now_ts is not None else int(time.time())
    try:
        venue_positions = await adapter.fetch_positions()
    except Exception as exc:  # noqa: BLE001 — read failed → skip, never blind
        logger.warning(
            "[qty-drift/alpaca] fetch_positions failed %r — skip pass", exc,
        )
        return 0
    venue_qty_by_symbol: dict[str, float] = {}
    for p in venue_positions or []:
        symbol = str(p.get("symbol") or "")
        if not symbol:
            continue
        try:
            venue_qty_by_symbol[symbol] = venue_qty_by_symbol.get(
                symbol, 0.0
            ) + abs(float(p.get("qty") or 0.0))
        except (TypeError, ValueError):
            continue
    if not venue_qty_by_symbol:
        return 0
    rows = conn.execute(
        "SELECT position_id, symbol, qty, risk_usd FROM positions "
        "WHERE venue = 'alpaca' AND status = 'open'"
    ).fetchall()
    internal_by_symbol: dict[str, list[tuple[str, float, float | None]]] = {}
    for position_id, symbol, qty, risk_usd in rows:
        internal_by_symbol.setdefault(str(symbol), []).append(
            (str(position_id), float(qty or 0.0), risk_usd)
        )
    touched = 0
    for symbol, venue_qty in venue_qty_by_symbol.items():
        positions_here = internal_by_symbol.get(symbol, [])
        internal_total = sum(q for _pid, q, _r in positions_here)
        diff = venue_qty - internal_total
        if abs(diff) * _entry_px_or_one(conn, positions_here) < ALPACA_QTY_DRIFT_DUST_USD:
            continue
        if not positions_here:
            # No tracked position at all for this symbol — not this function's
            # concern (the whole-venue-qty is an untracked orphan; the
            # adopt-import path owns creating a fresh tracked row for it).
            continue
        if diff > 0.0:
            if await _fold_qty_trueup(
                conn, adapter, symbol=symbol, positions=positions_here,
                now_ts=now,
            ):
                touched += 1
        else:
            if _clamp_qty_down(
                conn, symbol=symbol, positions=positions_here,
                venue_qty=venue_qty, now_ts=now,
            ):
                touched += 1
    return touched


def _entry_px_or_one(
    conn: sqlite3.Connection, positions: list[tuple[str, float, float | None]],
) -> float:
    """A rough $/share for the dust-floor check — the first position's own
    entry fill price, or ``1.0`` (never divides / never blocks on a miss)."""
    if not positions:
        return 1.0
    position_id = positions[0][0]
    row = conn.execute(
        "SELECT fill_price FROM fills WHERE contribution_id = ? AND is_close = 0 "
        "LIMIT 1",
        (position_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return 1.0
    try:
        px = float(row[0])
    except (TypeError, ValueError):
        return 1.0
    return px if px > 0.0 else 1.0


async def _fold_qty_trueup(
    conn: sqlite3.Connection, adapter: Any, *, symbol: str,
    positions: list[tuple[str, float, float | None]], now_ts: int,
) -> bool:
    """Attribute a venue qty surplus to a tracked position's OWN entry order.

    Tries each tracked position's entry-fill ``order_id`` in turn; the FIRST
    one whose venue ``filled_qty`` exceeds its internal qty is folded (mirrors
    ``true_up_alpaca_partial``'s fold shape — qty/fills/risk_usd rescale, entry
    price anchor kept). Returns ``True`` iff a fold happened.
    """
    for position_id, old_qty, old_risk_usd in positions:
        fill_row = conn.execute(
            "SELECT order_id, fill_price FROM fills "
            "WHERE contribution_id = ? AND is_close = 0 LIMIT 1",
            (position_id,),
        ).fetchone()
        if fill_row is None or not fill_row[0]:
            continue
        order_id = str(fill_row[0])
        try:
            row = await adapter.fetch_order(order_id=order_id)
        except Exception as exc:  # noqa: BLE001 — best-effort per position
            logger.warning(
                "[qty-drift/alpaca] %s order %s fetch failed %r — skip",
                symbol, order_id, exc,
            )
            continue
        try:
            final_qty = float((row or {}).get("filled_qty") or 0.0)
            final_px = float((row or {}).get("filled_avg_price") or 0.0)
        except (TypeError, ValueError):
            continue
        if final_qty <= old_qty or final_px <= 0.0:
            continue
        new_risk_usd = (
            float(old_risk_usd) * (final_qty / old_qty)
            if old_risk_usd is not None and old_qty > 0.0 else None
        )
        new_quote_qty = final_qty * final_px
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE positions SET qty = ?, risk_usd = COALESCE(?, risk_usd) "
                "WHERE position_id = ?",
                (final_qty, new_risk_usd, position_id),
            )
            conn.execute(
                "UPDATE fills SET base_qty = ?, size_usd = ?, quote_qty = ? "
                "WHERE order_id = ? AND is_close = 0",
                (final_qty, new_quote_qty, new_quote_qty, order_id),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            logger.error(
                "[qty-drift/alpaca] %s position=%s fold UPDATE failed "
                "(state preserved): %r", symbol, position_id, exc,
            )
            return False
        audit = json.dumps(
            {
                "venue": "alpaca", "symbol": symbol, "position_id": position_id,
                "order_id": order_id, "old_qty": old_qty, "new_qty": final_qty,
                "delta": final_qty - old_qty,
                "reason": "venue filled_qty exceeds internal qty (partial-fill "
                          "truncation) — folded into tracked position",
            },
            separators=(",", ":"),
        )
        with contextlib.suppress(sqlite3.Error):
            conn.execute(
                "INSERT INTO risk_events "
                "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
                "VALUES (?, ?, 'qty_trueup', ?, ?)",
                (uuid.uuid4().hex, "equity", now_ts, audit),
            )
            conn.commit()
        logger.warning(
            "[qty-drift/alpaca] %s position=%s qty %.9f -> %.9f (delta=%.9f "
            "folded, risk_usd rescaled, order=%s)",
            symbol, position_id, old_qty, final_qty, final_qty - old_qty, order_id,
        )
        return True
    return False


def _clamp_qty_down(
    conn: sqlite3.Connection, *, symbol: str,
    positions: list[tuple[str, float, float | None]], venue_qty: float,
    now_ts: int,
) -> bool:
    """Clamp the internal qty DOWN to the venue total (never fabricates a fill).

    Single-position case (the common one): clamp that position's qty straight
    to ``venue_qty``. Multi-position same-symbol case: clamp the LARGEST
    position only (proportional multi-way clamp is a rare edge the fold path
    does not need to solve precisely — the dollar drift is bounded by the
    single clamp either way, never worse than leaving it over-counted).
    """
    if not positions:
        return False
    position_id, old_qty, old_risk_usd = max(positions, key=lambda p: p[1])
    if venue_qty < 0.0 or old_qty <= venue_qty:
        return False
    new_risk_usd = (
        float(old_risk_usd) * (venue_qty / old_qty)
        if old_risk_usd is not None and old_qty > 0.0 else None
    )
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE positions SET qty = ?, risk_usd = COALESCE(?, risk_usd) "
            "WHERE position_id = ?",
            (venue_qty, new_risk_usd, position_id),
        )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        logger.error(
            "[qty-drift/alpaca] %s position=%s clamp UPDATE failed "
            "(state preserved): %r", symbol, position_id, exc,
        )
        return False
    audit = json.dumps(
        {
            "venue": "alpaca", "symbol": symbol, "position_id": position_id,
            "old_qty": old_qty, "new_qty": venue_qty,
            "reason": "internal qty exceeds venue total — clamped down "
                      "(fill fabrication forbidden)",
        },
        separators=(",", ":"),
    )
    with contextlib.suppress(sqlite3.Error):
        conn.execute(
            "INSERT INTO risk_events "
            "(risk_event_id, strategy_id, event_type, created_ts, payload_json) "
            "VALUES (?, ?, 'qty_drift_clamp', ?, ?)",
            (uuid.uuid4().hex, "equity", now_ts, audit),
        )
        conn.commit()
    logger.warning(
        "[qty-drift/alpaca] %s position=%s qty %.9f -> %.9f (clamped to venue, "
        "over-count drift-counter)",
        symbol, position_id, old_qty, venue_qty,
    )
    return True
