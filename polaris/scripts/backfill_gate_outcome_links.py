"""Offline one-shot backfill — historic gate→outcome links (bot STOPPED).

DEMO/PAPER. The live wiring (``_production_counterfactual``) links new runs as
they happen; this manual script repairs the PAST so the G3/G4 PASS cohort is
not empty at /debate time:

1. ``backfill_positions_pnl_r`` — stamps ``positions.pnl_r`` for already-CLOSED
   legacy positions from their lineage segment (``record_segment_close`` stamped
   ``pnl_r`` on every close; the latest ended segment carries the final value).
2. ``backfill_gate_event_links`` — links historic ``gate_events.position_id``
   via ``positions.signal_id``, ONLY when the signal_id maps to EXACTLY ONE
   position (tick-engine generic signal_ids are ambiguous → skipped + counted)
   and the gate row's created_ts sits within ±10 min of ``opened_ts`` (gate
   rows are wall-clock stamped during the GPT calls, so they can land seconds
   AFTER the tick-start ``opened_ts`` — the window is symmetric).

READ-MODEL ONLY: never run while the loop is live (it opens its own
connection); reruns are idempotent (NULL-guards). Usage:

    python3 -m polaris.scripts.backfill_gate_outcome_links --db data/polaris.sqlite
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

from polaris.storage.schema import init_db

logger = logging.getLogger(__name__)

# Symmetric link window around opened_ts (gate created_ts can trail the
# tick-start opened_ts by GPT latency; 600s also covers the design's pre-open
# window).
LINK_WINDOW_SEC = 600


def backfill_positions_pnl_r(conn: sqlite3.Connection) -> int:
    """Stamp pnl_r for closed positions from their latest ended lineage segment.

    Only touches rows where pnl_r IS NULL (idempotent) and a finished segment
    exists. Returns rows updated.
    """
    cur = conn.execute(
        """
        UPDATE positions SET pnl_r = (
            SELECT s.pnl_r FROM position_strategy_segments s
            WHERE s.position_id = positions.position_id
              AND s.ended_ts IS NOT NULL
            ORDER BY s.ended_ts DESC LIMIT 1
        )
        WHERE status = 'closed' AND pnl_r IS NULL
          AND EXISTS (
            SELECT 1 FROM position_strategy_segments s
            WHERE s.position_id = positions.position_id
              AND s.ended_ts IS NOT NULL
          )
        """
    )
    return int(cur.rowcount)


def backfill_gate_event_links(conn: sqlite3.Connection) -> dict[str, int]:
    """Link historic NULL-position_id gate_events rows via unique signal_id.

    A signal_id carried by MORE than one position (tick-engine generic ids) is
    ambiguous and skipped (counted in ``ambiguous_signal_ids`` so the residual
    un-linked PASS sample is visible). Returns counters.
    """
    stats = {"linked_rows": 0, "linked_positions": 0, "ambiguous_signal_ids": 0}
    rows = conn.execute(
        "SELECT signal_id, COUNT(*) FROM positions "
        "WHERE signal_id != '' GROUP BY signal_id"
    ).fetchall()
    for signal_id, n in rows:
        if int(n) != 1:
            stats["ambiguous_signal_ids"] += 1
            continue
        pos = conn.execute(
            "SELECT position_id, opened_ts FROM positions WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
        if pos is None:
            continue
        cur = conn.execute(
            "UPDATE gate_events SET position_id = ? "
            "WHERE signal_id = ? AND position_id IS NULL "
            "AND created_ts BETWEEN ? AND ?",
            (
                str(pos[0]), str(signal_id),
                int(pos[1]) - LINK_WINDOW_SEC, int(pos[1]) + LINK_WINDOW_SEC,
            ),
        )
        if cur.rowcount > 0:
            stats["linked_rows"] += int(cur.rowcount)
            stats["linked_positions"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backfill_gate_outcome_links")
    parser.add_argument("--db", type=Path, default=Path("data/polaris.sqlite"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    conn = init_db(args.db)  # applies the pnl_r migration on legacy DBs
    try:
        pnl_rows = backfill_positions_pnl_r(conn)
        link_stats = backfill_gate_event_links(conn)
    finally:
        conn.close()
    logger.info("positions.pnl_r backfilled : %d", pnl_rows)
    logger.info("gate_events rows linked    : %d", link_stats["linked_rows"])
    logger.info("positions linked           : %d", link_stats["linked_positions"])
    logger.info(
        "ambiguous signal_ids skipped: %d (tick-engine generic ids — "
        "permanently un-linked, sample reported for the cohort query)",
        link_stats["ambiguous_signal_ids"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
