"""T12 cleanup — force close CAP 24h+ stuck positions (structural defect).

Usage:
    python3 -m scripts.force_close_cap_24h [--dry-run]

Jin 승인 옵션 B. CAP adapter.close_position 직접 호출.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "invasion.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO))

    # Age > 24h, CAP exchange, status='open'
    with sqlite3.connect(str(DB)) as conn:
        rows = conn.execute("""
            SELECT ticker, direction, asset_group,
                   ROUND((strftime('%s','now')-entry_ts)/60.0, 0) age_min,
                   size_usd
            FROM trades
            WHERE status='open' AND exchange='cap'
              AND (strftime('%s','now')-entry_ts) > 86400
            ORDER BY entry_ts
        """).fetchall()

    if not rows:
        print("No CAP 24h+ positions.")
        return 0

    print(f"CAP 24h+ positions to close: {len(rows)}")
    for r in rows:
        print(f"  {r[0]:20s} {r[1]:5s} {r[2]:10s} age={r[3]}m size=${r[4]:.0f}")

    if args.dry_run:
        print("\n[DRY-RUN]")
        return 0

    from invasion.config.config import Config
    from invasion.exchange.capital.client import CapitalComClient
    from invasion.exchange.capital_adapter import CapitalComAdapter

    cfg = Config()
    cap_client = CapitalComClient(cfg)
    cap_adapter = CapitalComAdapter(cap_client, cfg=cfg)

    closed = []
    failed = []
    for ticker, direction, group, age_min, size in rows:
        try:
            cap_adapter.close_position(ticker, "CLEANUP_STRUCT_DEFECT_T12")
            closed.append(ticker)
            print(f"  closed: {ticker}")
        except Exception as e:
            failed.append((ticker, str(e)))
            print(f"  FAIL: {ticker}: {e}")

    # DB stamp
    if closed:
        with sqlite3.connect(str(DB)) as conn:
            placeholders = ",".join("?" * len(closed))
            conn.execute(f"""
                UPDATE trades
                SET status='quarantined_structural_defect',
                    exit_ts=strftime('%s','now'),
                    exit_type='CLEANUP_STRUCT_DEFECT_T12'
                WHERE status='open' AND ticker IN ({placeholders})
            """, closed)
            conn.commit()

    print(f"\n== Summary ==")
    print(f"  closed: {len(closed)} / failed: {len(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
