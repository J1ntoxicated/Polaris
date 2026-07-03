"""Layer 3 — Profit-sweep ladder (1단 close → 3단 Alpaca auto-draw).

Spec source: vault/50_research/debates/profit_sweep_ladder_2026-07-03.md
(codex 3-round consensus, DEMO/PAPER OKX/Capital/Alpaca).

``ladder_ledger`` is an append-only event log (CREDIT / DRAW / RELEASE);
every balance is a ``SUM()`` PROJECTION over these rows — there is no mutable
aggregate column, so a crash mid-write can never corrupt the balance (replay
just re-derives it). Three entry points:

* :func:`materialize_credits` — scans CLOSED positions, computes the
  per-position NET realized ``pnl_usd`` (partial-fill slices already offset
  within the same position via ``fills.contribution_id``), and inserts ONE
  CREDIT row per profitable position (``sweep_pct`` share). Idempotent via
  the ``source_position_id`` UNIQUE partial index. Called on-demand (right
  before a 3단 draw) AND by a ~5-minute sweeper — NEVER inline in the
  position-close transaction (feedback_db_lock_is_architecture_signal: the
  close hot path gets zero new writes from this module).
* :func:`draw_for_signal` — the 3단 (Alpaca) auto-draw. Runs inside a
  ``BEGIN IMMEDIATE`` serial transaction so concurrent signals cannot read
  the same ``bucket_available_usd`` and double-spend it. ``draw_used =
  max(0, min(bucket_available, needed, fresh_bp - pending))`` — pure
  ``min()`` composition, no new multiplier (9-stack ban intact); the caller
  folds the result into the EXISTING single-trade ``min()`` cap slot as
  ``cap_base + draw_used``.
* :func:`release_stale_draws` — reconcile pass (startup + periodic). A DRAW
  releases when its (venue, symbol, strategy_id) has neither an OPEN
  position nor a live/pending order (``pending_opens``) — covers the
  reject/cancel/expiry path too, since those never open a position. RELEASE
  NEVER creates credit (loss_debit=0 invariant) — it only restores what the
  matching DRAW consumed.
"""

from __future__ import annotations

import math
import os
import sqlite3
import uuid
from typing import Final

__all__ = [
    "SWEEP_PCT_DEFAULT",
    "bucket_available_usd",
    "draw_for_signal",
    "materialize_credits",
    "release_stale_draws",
    "sweep_pct",
]

SWEEP_PCT_DEFAULT: Final[float] = 0.50
"""Consensus X — % of a closed position's NET realized profit that becomes
draw-able bucket. Env: ``POLARIS_LADDER_SWEEP_PCT``. This is a capital-
recycling unlock-rate knob (feeds the SAME 3단 single-trade min() slot), NOT
a T4 chain multiplier."""

_SWEEP_PCT_ENV: Final[str] = "POLARIS_LADDER_SWEEP_PCT"

# 3단 draw is scoped to Alpaca only per the debate spec (§⑤ scope: 1단→3단
# only, 2단/T4-judge untouched).
_DRAW_VENUE: Final[str] = "alpaca"


def sweep_pct() -> float:
    raw = os.environ.get(_SWEEP_PCT_ENV)
    if raw is None or raw == "":
        return SWEEP_PCT_DEFAULT
    try:
        v = float(raw)
    except ValueError:
        return SWEEP_PCT_DEFAULT
    return v if math.isfinite(v) and 0.0 <= v <= 1.0 else SWEEP_PCT_DEFAULT


# ---------------------------------------------------------------------------
# CREDIT materializer
# ---------------------------------------------------------------------------


def _net_pnl_usd_for_position(conn: sqlite3.Connection, position_id: str) -> float:
    """Per-position NET realized ``pnl_usd`` — SUM over ALL close fills of the
    position (partial-fill slices net against each other; +100/-150 → -50).
    Mirrors ``_production_close_helpers.close_pnl_usd_total``'s query (core
    cannot import that scripts-layer helper — ``SimulatedTrade``-coupled)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(pnl_usd), 0.0) FROM fills "
        "WHERE contribution_id = ? AND is_close = 1",
        (position_id,),
    ).fetchone()
    return float(row[0]) if row is not None else 0.0


def materialize_credits(conn: sqlite3.Connection, *, now_ts: int) -> int:
    """Scan CLOSED positions since the checkpoint, insert one CREDIT row per
    profitable position's net PnL. Returns the count of NEW CREDIT rows.

    Idempotent: ``ladder_ledger`` has a UNIQUE partial index on
    ``source_position_id WHERE event_type='CREDIT'`` — a re-scan of an
    already-credited position is a silent no-op (``INSERT OR IGNORE``). The
    checkpoint only advances after a batch's inserts all succeed, so a crash
    mid-batch safely re-scans (never skips, never double-credits — the
    UNIQUE index is the actual idempotency guarantee, the checkpoint is a
    pure scan-window optimization)."""
    checkpoint_row = conn.execute(
        "SELECT last_scanned_closed_ts FROM ladder_credit_checkpoint WHERE id = 1"
    ).fetchone()
    watermark = int(checkpoint_row[0]) if checkpoint_row is not None else 0

    positions = conn.execute(
        "SELECT position_id, venue FROM positions "
        "WHERE status = 'closed' AND closed_ts IS NOT NULL AND closed_ts >= ? "
        "ORDER BY closed_ts ASC",
        (watermark,),
    ).fetchall()

    inserted = 0
    max_closed_ts = watermark
    pct = sweep_pct()
    for position_id, venue in positions:
        net_pnl = _net_pnl_usd_for_position(conn, position_id)
        if net_pnl > 0.0:
            amount = net_pnl * pct
            event_id = f"credit-{position_id}"
            cur = conn.execute(
                "INSERT OR IGNORE INTO ladder_ledger "
                "(event_id, event_type, source_position_id, venue, amount_usd, "
                "reason, created_ts) VALUES (?, 'CREDIT', ?, ?, ?, 'net_pnl_sweep', ?)",
                (event_id, position_id, venue or "", amount, now_ts),
            )
            if cur.rowcount > 0:
                inserted += 1
        row = conn.execute(
            "SELECT closed_ts FROM positions WHERE position_id = ?", (position_id,)
        ).fetchone()
        if row is not None and row[0] is not None:
            max_closed_ts = max(max_closed_ts, int(row[0]))

    conn.execute(
        "INSERT INTO ladder_credit_checkpoint (id, last_scanned_closed_ts) "
        "VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET last_scanned_closed_ts = "
        "MAX(last_scanned_closed_ts, excluded.last_scanned_closed_ts)",
        (max_closed_ts,),
    )
    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# Bucket balance (SUM projection — no aggregate mutable column)
# ---------------------------------------------------------------------------


def bucket_available_usd(conn: sqlite3.Connection) -> float:
    """Current draw-able balance: CREDIT + RELEASE − DRAW, summed live."""
    row = conn.execute(
        "SELECT "
        "COALESCE(SUM(CASE WHEN event_type IN ('CREDIT', 'RELEASE') THEN amount_usd "
        "ELSE -amount_usd END), 0.0) FROM ladder_ledger"
    ).fetchone()
    return max(0.0, float(row[0]) if row is not None else 0.0)


# ---------------------------------------------------------------------------
# 3단 auto-draw
# ---------------------------------------------------------------------------


def draw_for_signal(
    conn: sqlite3.Connection,
    *,
    signal_id: str,
    venue: str,
    symbol: str,
    strategy_id: str,
    needed_usd: float,
    fresh_bp_usd: float,
    pending_usd: float,
    now_ts: int,
) -> tuple[float, str]:
    """Auto-draw for a 3단 (Alpaca) sizing signal. Threshold=0 — every signal
    on the draw venue attempts a draw (no hidden blocker).

    ``draw_used = max(0, min(bucket_available, needed, fresh_bp - pending))``
    — pure ``min()`` term, composed into the caller's EXISTING single-trade
    cap slot as ``cap_base + draw_used`` (no new T4 multiplier).

    Runs inside ``BEGIN IMMEDIATE`` so the read of ``bucket_available_usd``
    and the DRAW insert are atomic — a concurrent second signal on another
    thread/process blocks until this transaction commits, so two signals can
    never both read the same available balance and jointly over-draw it.

    Returns ``(drawn_usd, draw_id)``; ``(0.0, "")`` when nothing is drawn
    (off-venue, non-finite/non-positive inputs, or an exhausted bucket) —
    never raises on a data-integrity gap (flow_not_block: the signal's normal
    %-equity sizing is untouched either way, this only ADDS headroom)."""
    if venue != _DRAW_VENUE:
        return (0.0, "")
    if not all(math.isfinite(x) for x in (needed_usd, fresh_bp_usd, pending_usd)):
        return (0.0, "")
    if needed_usd <= 0.0:
        return (0.0, "")

    conn.execute("BEGIN IMMEDIATE")
    try:
        available = bucket_available_usd(conn)
        headroom = fresh_bp_usd - pending_usd
        draw_used = max(0.0, min(available, needed_usd, headroom))
        if draw_used <= 0.0:
            conn.execute("COMMIT")
            return (0.0, "")
        draw_id = f"draw-{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO ladder_ledger "
            "(event_id, event_type, draw_id, venue, symbol, strategy_id, "
            "signal_id, amount_usd, reason, created_ts) "
            "VALUES (?, 'DRAW', ?, ?, ?, ?, ?, ?, 'auto_draw_3rd_tier', ?)",
            (draw_id, draw_id, venue, symbol, strategy_id, signal_id, draw_used, now_ts),
        )
        conn.execute("COMMIT")
        return (draw_used, draw_id)
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# RELEASE reconcile
# ---------------------------------------------------------------------------


def _has_open_position(conn: sqlite3.Connection, *, venue: str, symbol: str, strategy_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM positions WHERE venue = ? AND symbol = ? AND strategy_id = ? "
        "AND status = 'open' LIMIT 1",
        (venue, symbol, strategy_id),
    ).fetchone()
    return row is not None


def _has_pending_order(conn: sqlite3.Connection, *, venue: str, symbol: str, strategy_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pending_opens WHERE venue = ? AND symbol = ? AND strategy_id = ? LIMIT 1",
        (venue, symbol, strategy_id),
    ).fetchone()
    return row is not None


def release_stale_draws(conn: sqlite3.Connection, *, now_ts: int) -> int:
    """Reconcile pass (startup + periodic): release every open DRAW whose
    (venue, symbol, strategy_id) has neither an open position NOR a live/
    pending order. Covers reject/cancel/expiry too — those paths never open
    a position, so they satisfy the SAME predicate immediately. Idempotent —
    a DRAW already released (has a RELEASE row keyed on the same ``draw_id``)
    is skipped. NEVER creates credit: the RELEASE amount is exactly the
    matching DRAW's ``amount_usd`` (restores headroom, manufactures nothing).

    Returns the count of NEW RELEASE rows inserted."""
    open_draws = conn.execute(
        "SELECT draw_id, venue, symbol, strategy_id, amount_usd FROM ladder_ledger "
        "WHERE event_type = 'DRAW' AND draw_id NOT IN ("
        "  SELECT draw_id FROM ladder_ledger WHERE event_type = 'RELEASE'"
        ")"
    ).fetchall()

    released = 0
    for draw_id, venue, symbol, strategy_id, amount_usd in open_draws:
        if _has_open_position(conn, venue=venue, symbol=symbol, strategy_id=strategy_id):
            continue
        if _has_pending_order(conn, venue=venue, symbol=symbol, strategy_id=strategy_id):
            continue
        event_id = f"release-{draw_id}"
        cur = conn.execute(
            "INSERT OR IGNORE INTO ladder_ledger "
            "(event_id, event_type, draw_id, venue, symbol, strategy_id, amount_usd, "
            "reason, created_ts) VALUES (?, 'RELEASE', ?, ?, ?, ?, ?, 'reconcile', ?)",
            (event_id, draw_id, venue, symbol, strategy_id, amount_usd, now_ts),
        )
        if cur.rowcount > 0:
            released += 1
    conn.commit()
    return released
