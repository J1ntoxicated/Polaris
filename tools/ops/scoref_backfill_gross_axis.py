"""One-shot backfill: reconstruct the gross axis for legacy score_f_events rows.

Closes the fee-split flip review's remaining MED (regression r3,
2026-07-12): 407/890 pre-v0 ledger rows carry NULL gross_usd/notional_usd/
fee_raw_usd, silently shrinking every gross intent_scores window right
after the flip (Capital worst: session_breakout 9/126). Reconstruction
reuses ``compute_lifecycle_fee`` — the exact writer the live close hook
uses — so backfilled rows are byte-compatible with post-flip rows
(fee-split flip canon: vault/50_research/debates/fee_split_flip_r2_2026-07-12.md).

Rows whose fills legs no longer exist (retention-trimmed) are left NULL —
they stay excluded from gross windows, same as before this tool.

Usage (bot stopped; idempotent — only touches rows still NULL):
    python3 -m tools.ops.scoref_backfill_gross_axis --db data/polaris_live.sqlite [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3

from polaris.core.classes.score_f import compute_lifecycle_fee


def backfill(conn: sqlite3.Connection, *, dry_run: bool = False) -> tuple[int, int]:
    """Returns (filled, unreconstructible)."""
    rows = conn.execute(
        "SELECT position_id, venue, closed_ts FROM score_f_events WHERE notional_usd IS NULL"
    ).fetchall()
    filled = 0
    missing = 0
    for position_id, venue, closed_ts in rows:
        inst = conn.execute(
            "SELECT instrument_id FROM fills WHERE contribution_id = ? LIMIT 1",
            (position_id,),
        ).fetchone()
        if inst is None or not inst[0] or ":" not in inst[0]:
            missing += 1
            continue
        symbol = str(inst[0]).split(":", 1)[1]
        fees = compute_lifecycle_fee(
            conn, position_id=position_id, venue=venue, symbol=symbol, closed_ts=int(closed_ts)
        )
        if fees.notional_usd <= 0.0:
            missing += 1
            continue
        if not dry_run:
            conn.execute(
                "UPDATE score_f_events SET gross_usd = ?, notional_usd = ?, fee_raw_usd = ? "
                "WHERE position_id = ?",
                (fees.net_usd, fees.notional_usd, fees.fee_usd, position_id),
            )
        filled += 1
    if not dry_run:
        conn.commit()
    return filled, missing


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        filled, missing = backfill(conn, dry_run=args.dry_run)
        total = conn.execute(
            "SELECT COUNT(*), SUM(notional_usd IS NULL) FROM score_f_events"
        ).fetchone()
        print(
            f"backfill{' (dry-run)' if args.dry_run else ''}: filled={filled} "
            f"unreconstructible={missing} | ledger total={total[0]} null-remaining={total[1]}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
