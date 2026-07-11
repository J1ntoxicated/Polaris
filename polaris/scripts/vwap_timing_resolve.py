"""Offline resolver — backfills ACTUAL fill price onto ``vwap_timing_shadow``
rows (frontgate-scan item #6, stage 2).

DEMO/PAPER, virtual capital. Stage 1
(``polaris.core.pipeline.agents.vwap_timing_shadow.log_vwap_timing_shadow``)
logs the G4 decision-time known-at-time session-VWAP / signal-day-AVWAP
distance. G4's decision-time mid predates G5 sizing/order placement and can
drift from the real fill (verifier note #3), so the promotion metric
("average improvement > 0 bps") must be judged on the ACTUAL fill, not the
G4-time mid — this stage 2 mirrors ``gate_kill_counterfactuals``'
``backfill_run_position_id`` / ``sweep_forward_marks`` two-stage precedent
(``_production_counterfactual.py``): join ``gate_events.position_id`` (already
stamped by that same backfill, run_id-scoped) -> ``positions.side`` ->
``fills.contribution_id = position_id AND is_close = 0`` for the entry fill.

Read/write ONLY ``vwap_timing_shadow`` — never any live gate/sizing/exit
input. Fail-open throughout; a run whose signal never opens a position is
terminated ``unresolvable`` after ``UNRESOLVABLE_GRACE_SEC`` so it cannot
starve the batch forever.

Usage (manual / cron, mirrors ``recalc_excursions.py``):
  python3 -m polaris.scripts.vwap_timing_resolve --db data/polaris_live.sqlite
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from typing import Final

logger = logging.getLogger(__name__)

__all__ = ["UNRESOLVABLE_GRACE_SEC", "resolve_vwap_timing_fills"]

# Terminal grace window (mirrors gate_kill_counterfactuals.UNRESOLVABLE_GRACE_SEC):
# a signal whose run never opens a position (G5 cap-killed, venue reject, focus
# rotation) stops occupying the pending batch after this long.
UNRESOLVABLE_GRACE_SEC: Final[int] = 3 * 86400
DEFAULT_LIMIT: Final[int] = 200


def _side_aware_improvement_bps(anchor: float, fill_price: float, side: str) -> float:
    """Positive = a BETTER-than-anchor fill (bought below VWAP / sold above it)."""
    if anchor <= 0.0:
        return 0.0
    if side == "long":
        return (anchor - fill_price) / anchor * 10_000.0
    return (fill_price - anchor) / anchor * 10_000.0


def _mark_attempt(conn: sqlite3.Connection, *, event_id: str, terminal: bool) -> None:
    if terminal:
        conn.execute(
            "UPDATE vwap_timing_shadow SET unresolvable = 1, "
            "resolve_attempts = resolve_attempts + 1 WHERE event_id = ?",
            (event_id,),
        )
    else:
        conn.execute(
            "UPDATE vwap_timing_shadow SET "
            "resolve_attempts = resolve_attempts + 1 WHERE event_id = ?",
            (event_id,),
        )


def resolve_vwap_timing_fills(
    conn: sqlite3.Connection, *, now_ts: int, limit: int = DEFAULT_LIMIT
) -> int:
    """Resolve pending ``vwap_timing_shadow`` rows from already-recorded fills.

    Returns the number of rows whose fill was found + backfilled this pass.
    Fail-open — never raises; a missing table / any ``sqlite3.Error`` on the
    read yields 0.
    """
    try:
        rows = conn.execute(
            """
            SELECT event_id, run_id, decision_ts, session_vwap, avwap
            FROM vwap_timing_shadow
            WHERE fill_price IS NULL AND unresolvable = 0
            ORDER BY decision_ts ASC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("[vwap-timing-resolve] read failed: %r", exc)
        return 0

    updated = 0
    for event_id, run_id, decision_ts, session_vwap, avwap in rows:
        try:
            terminal = int(decision_ts) + UNRESOLVABLE_GRACE_SEC <= now_ts
            pos_row = conn.execute(
                "SELECT position_id FROM gate_events WHERE run_id = ? "
                "AND gate_id = 4 AND position_id IS NOT NULL LIMIT 1",
                (run_id,),
            ).fetchone()
            if pos_row is None:
                _mark_attempt(conn, event_id=event_id, terminal=terminal)
                continue
            position_id = str(pos_row[0])
            side_row = conn.execute(
                "SELECT side FROM positions WHERE position_id = ?", (position_id,)
            ).fetchone()
            side = str(side_row[0]) if side_row else "long"
            fill_row = conn.execute(
                "SELECT fill_price, ts_ms FROM fills WHERE contribution_id = ? "
                "AND is_close = 0 ORDER BY ts_ms ASC LIMIT 1",
                (position_id,),
            ).fetchone()
            if fill_row is None:
                _mark_attempt(conn, event_id=event_id, terminal=terminal)
                continue
            fill_price, fill_ts_ms = float(fill_row[0]), int(fill_row[1])
            session_imp = (
                _side_aware_improvement_bps(float(session_vwap), fill_price, side)
                if session_vwap is not None else None
            )
            avwap_imp = (
                _side_aware_improvement_bps(float(avwap), fill_price, side)
                if avwap is not None else None
            )
            conn.execute(
                "UPDATE vwap_timing_shadow SET fill_price = ?, fill_ts = ?, side = ?, "
                "session_vwap_improvement_bps = ?, avwap_improvement_bps = ?, "
                "resolve_attempts = resolve_attempts + 1 WHERE event_id = ?",
                (fill_price, fill_ts_ms, side, session_imp, avwap_imp, event_id),
            )
            updated += 1
        except sqlite3.Error as exc:
            logger.warning(
                "[vwap-timing-resolve] row dropped event_id=%s: %r", event_id, exc
            )
            continue
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite path (must exist)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    import time

    conn = sqlite3.connect(args.db, isolation_level=None)
    try:
        n = resolve_vwap_timing_fills(conn, now_ts=int(time.time()), limit=args.limit)
        print(f"resolved {n} vwap_timing_shadow row(s)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
