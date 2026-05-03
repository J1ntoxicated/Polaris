"""T12 cleanup — force close Alpaca 24h+ stuck positions (structural defect).

Usage:
    python3 -m scripts.force_close_alpaca_24h [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "invasion.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO))

    with sqlite3.connect(str(DB)) as conn:
        rows = conn.execute("""
            SELECT ticker, direction, asset_group,
                   ROUND((strftime('%s','now')-entry_ts)/60.0, 0) age_min,
                   size_usd
            FROM trades
            WHERE status='open' AND exchange='alpaca'
              AND (strftime('%s','now')-entry_ts) > 86400
            ORDER BY entry_ts
        """).fetchall()

    if not rows:
        print("No Alpaca 24h+.")
        return 0

    print(f"Alpaca 24h+ to close: {len(rows)}")
    if args.dry_run:
        for r in rows[:5]:
            print(f"  {r[0]} {r[1]} age={r[3]}m")
        print(f"  ... ({len(rows)-5} more)")
        return 0

    from invasion.config.config import Config
    from invasion.exchange.alpaca_adapter import AlpacaAdapter
    from invasion.exchange.alpaca.client import AlpacaClient

    cfg = Config()
    client = AlpacaClient(cfg)
    adapter = AlpacaAdapter(client, cfg=cfg)

    closed = []
    failed = []
    for ticker, direction, group, age_min, size in rows:
        try:
            result = adapter.close_position(ticker, "CLEANUP_STRUCT_DEFECT_T12")
            if result is not None:
                closed.append(ticker)
                print(f"  closed: {ticker}")
            else:
                failed.append((ticker, "close_position returned None"))
        except Exception as e:
            failed.append((ticker, str(e)[:80]))
            print(f"  FAIL: {ticker}: {str(e)[:80]}")

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

    print(f"\n== Summary: closed {len(closed)} / failed {len(failed)} ==")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
