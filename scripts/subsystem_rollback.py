#!/usr/bin/env python3
"""Emergency rollback for subsystem review actions.

Usage:
  python3 scripts/subsystem_rollback.py --provider=all        # 전체 provider active=1
  python3 scripts/subsystem_rollback.py --provider=macro_regime  # 단일 provider
  python3 scripts/subsystem_rollback.py --strategy=crypto_momentum_reversal
  python3 scripts/subsystem_rollback.py --list                # 현재 disabled 목록만
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def list_disabled():
    conn = sqlite3.connect("data/invasion.sqlite", timeout=3)
    try:
        print("\n=== DISABLED providers ===")
        cur = conn.execute(
            "SELECT name, pnl_attribution_7d, trade_count_7d FROM signal_providers "
            "WHERE active=0 ORDER BY trade_count_7d DESC"
        )
        for row in cur.fetchall():
            print(f"  {row[0]:30} pnl_7d=${row[1]:.1f} trades_7d={row[2]}")
        print("\n=== DISABLED strategies ===")
        cur = conn.execute(
            "SELECT name FROM strategies WHERE status='disabled' ORDER BY name"
        )
        for row in cur.fetchall():
            print(f"  {row[0]}")
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--provider", help="provider 이름 또는 'all'")
    p.add_argument("--strategy", help="strategy 이름")
    p.add_argument("--list", action="store_true", help="현재 disabled 목록")
    args = p.parse_args()

    if args.list:
        list_disabled()
        return

    from invasion.evolution.review_actions import (
        rollback_providers_all, rollback_strategy_db,
    )

    if args.provider == "all":
        n = rollback_providers_all()
        print(f"✓ Rolled back {n} providers to active=1")
    elif args.provider:
        import sqlite3 as _sq
        conn = _sq.connect("data/invasion.sqlite", timeout=3)
        try:
            conn.execute(
                "UPDATE signal_providers SET active=1 WHERE name=?",
                (args.provider,),
            )
            changed = conn.total_changes
            conn.commit()
        finally:
            conn.close()
        print(f"✓ Rolled back provider {args.provider} ({changed} row)")
    elif args.strategy:
        ok = rollback_strategy_db(args.strategy)
        print(f"{'✓' if ok else '⚠'} Strategy {args.strategy} rollback: {ok}")
    else:
        p.print_help()


if __name__ == "__main__":
    main()
