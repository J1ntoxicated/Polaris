"""One-off ops: purge orphaned ``position_risk_state`` rows (ghost-row RCA).

Root cause (2026-07-10, Capital track-B exposure starvation): FOUR
terminal-transition paths (``_production_close._finalize_already_closed``,
``_production_close_helpers._reconcile_orphan``,
``reconcile_alpaca_zombies.{reconcile_alpaca_zombies,
reconcile_alpaca_venue_drift}``, ``correct_close_pnl_stamping.py``'s
``--fix-status`` branch) moved a position OUT of ``status='open'`` without
calling ``delete_position_risk_state`` — each is now fixed going forward
(same commit as this script). This script corrects the HISTORICAL rows those
gaps already left behind on the live DB: a ``position_risk_state`` row whose
own PK (venue, symbol, strategy, opened_ts) no longer matches a ``positions``
row with ``status='open'`` is dead weight permanently consuming
per-symbol/underlying/cluster/track sizing headroom (the traced incident:
Capital track B read Σopen_risk_pct=282.91% against a 100% cap from 56 such
ghost rows — 357 cluster-binding ``sizing_zero`` KILLs / 24h).

``_sizer_payload._read_portfolio_state`` now ALSO ignores these rows directly
(a JOIN-based 2nd line of defense, same commit) — so headroom is already
correct at read time regardless of whether this script has run. This script
only reclaims the TABLE itself (bloat + so a future direct read of
``position_risk_state``, like this incident's own diagnostic queries, is not
misleading).

DEMO/PAPER only — virtual funds. Dry-run by default (read-only URI, zero
writes); ``--apply`` backs the DB up first and deletes in a single
transaction with one aggregate ``risk_events`` audit row. Idempotent: a
second ``--apply`` finds zero ghosts. Run with the bot STOPPED (``BEGIN
IMMEDIATE`` aborts if another writer holds the DB).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from polaris.scripts.correct_close_pnl_stamping import backup_db


@dataclass(slots=True)
class GhostRow:
    venue: str
    symbol: str
    strategy: str
    opened_ts: int
    open_risk_pct: float
    notional_usd: float
    cluster_id: str | None
    track: str
    matched_status: str | None  # None = no positions row at all


def analyze(conn: sqlite3.Connection) -> list[GhostRow]:
    """Every ``position_risk_state`` row whose PK doesn't match a live
    ``positions.status='open'`` row — LEFT JOIN so both "matched but
    terminal" and "no matching positions row at all" are caught."""
    rows = conn.execute(
        """
        SELECT prs.venue, prs.symbol, prs.strategy, prs.opened_ts,
               prs.open_risk_pct, prs.notional_usd, prs.cluster_id, prs.track,
               p.status
        FROM position_risk_state prs
        LEFT JOIN positions p
            ON p.venue = prs.venue AND p.symbol = prs.symbol
            AND p.strategy_id = prs.strategy AND p.opened_ts = prs.opened_ts
        WHERE p.status IS NULL OR p.status != 'open'
        ORDER BY prs.venue, prs.opened_ts ASC
        """,
    ).fetchall()
    return [
        GhostRow(
            venue=str(r[0]), symbol=str(r[1]), strategy=str(r[2]),
            opened_ts=int(r[3]), open_risk_pct=float(r[4]),
            notional_usd=float(r[5]), cluster_id=r[6], track=str(r[7]),
            matched_status=r[8],
        )
        for r in rows
    ]


def apply_corrections(
    conn: sqlite3.Connection, ghosts: list[GhostRow], *,
    backup_path: str, now_ts: int,
) -> int:
    """Delete every ghost row in one transaction + an aggregate audit row.
    Returns the number of rows deleted."""
    if not ghosts:
        return 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for g in ghosts:
            conn.execute(
                "DELETE FROM position_risk_state "
                "WHERE venue = ? AND symbol = ? AND strategy = ? AND opened_ts = ?",
                (g.venue, g.symbol, g.strategy, g.opened_ts),
            )
        payload = json.dumps(
            {
                "n_deleted": len(ghosts),
                "by_venue": {
                    v: sum(1 for g in ghosts if g.venue == v)
                    for v in sorted({g.venue for g in ghosts})
                },
                "sum_open_risk_pct_freed": sum(g.open_risk_pct for g in ghosts),
                "backup_path": backup_path,
            },
            separators=(",", ":"),
        )
        conn.execute(
            "INSERT INTO risk_events (risk_event_id, strategy_id, "
            "event_type, created_ts, payload_json) "
            "VALUES (?, '_ops', 'position_risk_state_ghost_purge', ?, ?)",
            (uuid.uuid4().hex, now_ts, payload),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    return len(ghosts)


def _print_report(ghosts: list[GhostRow]) -> None:
    print(f"ghost position_risk_state rows: {len(ghosts)}")
    if not ghosts:
        return
    by_venue: dict[str, float] = {}
    for g in ghosts:
        by_venue[g.venue] = by_venue.get(g.venue, 0.0) + g.open_risk_pct
    for venue, total in sorted(by_venue.items()):
        n = sum(1 for g in ghosts if g.venue == venue)
        print(f"  {venue:<10} rows={n:<4} Σopen_risk_pct_freed={total:.4f}")
    hdr = (f"{'venue':<10} {'symbol':<12} {'strategy':<22} {'opened_ts':>12} "
           f"{'open_risk_pct':>14} {'matched_status':>15}")
    print(hdr)
    for g in ghosts:
        print(
            f"{g.venue:<10} {g.symbol:<12} {g.strategy:<22} {g.opened_ts:>12} "
            f"{g.open_risk_pct:>14.4f} {g.matched_status or '(no positions row)':>15}"
        )


def run(*, db_path: str, apply: bool) -> int:
    db = Path(db_path)
    if not db.exists():
        print(f"DB not found: {db}")
        return 1
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"db={db}  mode={mode}")
    backup_path = ""
    if apply:
        backup_path = backup_db(db)
        print(f"backup -> {backup_path}")
        conn = sqlite3.connect(db)
    else:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        ghosts = analyze(conn)
        _print_report(ghosts)
        if not apply:
            print(f"\n[dry-run] {len(ghosts)} ghost rows would be purged. "
                  f"Re-run with --apply (bot stopped).")
            return 0
        now_ts = int(time.time())
        n = apply_corrections(conn, ghosts, backup_path=backup_path, now_ts=now_ts)
        print(f"\n[apply] purged {n} ghost rows")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="correct_position_risk_state_ghosts")
    p.add_argument("--db", default="data/polaris_live.sqlite",
                   help="SQLite DB path (default: data/polaris_live.sqlite).")
    p.add_argument("--apply", action="store_true", default=False,
                   help="Write corrections (default: dry-run, read-only URI).")
    args = p.parse_args(argv)
    return run(db_path=args.db, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
