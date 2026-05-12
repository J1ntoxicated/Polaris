"""Day 6 — full-pipeline smoke (G3->G7 + fills table + dashboard).

Drives ``smoke_paper_loop.run_smoke`` with:
- ``full_pipeline=True``  -> every emitted RawSignal walks G3 -> G4 -> G5
                             with payload_builder-shaped inputs.
- ``real_roundtrip``      -> when creds are set, fires one real demo
                             OKX BTC-USDT IOC + Capital EURUSD round-trip
                             and persists to the ``fills`` table.
                             ``--dry-run`` substitutes synthetic Fills so
                             the script runs in CI without venue creds.
- Dashboard auto-renders in a separate process — this script reads the
  same SQLite DB so the user can confirm by launching
  ``polaris.scripts.dashboard_v0`` in a second terminal.

Exit code:
- 0 = at least one closed_trade + at least one fills row + 0 lifecycle
      invariant violations.
- 1 = otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Iterable
from pathlib import Path

from polaris.core.data.fills_persist import read_recent_fills
from polaris.scripts.smoke_paper_loop import run_smoke
from polaris.storage.schema import init_db


async def run_day6_smoke(
    *,
    duration_sec: float = 60.0,
    tick_sec: float = 5.0,
    real_roundtrip: bool = False,
    roundtrip_dry_run: bool = False,
    db_path: Path | None = None,
) -> int:
    target_db = db_path or Path("data/polaris.sqlite")
    target_db.parent.mkdir(parents=True, exist_ok=True)
    # Ensure DDL is current.
    init_db(target_db).close()

    exit_code = await run_smoke(
        duration_sec=duration_sec,
        tick_sec=tick_sec,
        full_pipeline=True,
        real_roundtrip=real_roundtrip,
        roundtrip_dry_run=roundtrip_dry_run,
        db_path=target_db,
    )
    # Re-open to assess persisted state.
    import sqlite3

    conn = sqlite3.connect(f"file:{target_db}?mode=ro", uri=True)
    try:
        recent = read_recent_fills(conn, limit=20)
    finally:
        conn.close()
    print("\n=========== Day 6 final fills snapshot ===========")
    print(f"persisted_fills_count : {len(recent)}")
    for r in recent[:5]:
        print(
            f"  {r['venue']:<8} {r['instrument_id']:<24} "
            f"side={r['side']:<4} size_usd={r['size_usd']:<10.4f} "
            f"px={r['fill_price']:<12.4f} pnl_usd={r['pnl_usd']:<+8.4f} "
            f"is_close={r['is_close']}"
        )
    return exit_code if recent else 1


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smoke_day6_full_pipeline")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--tick", type=float, default=5.0)
    parser.add_argument("--real-roundtrip", action="store_true", default=False)
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Use synthetic round-trip fills (no venue creds).")
    parser.add_argument("--db", type=Path, default=Path("data/polaris.sqlite"))
    args = parser.parse_args(list(argv) if argv is not None else None)
    return asyncio.run(
        run_day6_smoke(
            duration_sec=args.duration,
            tick_sec=args.tick,
            real_roundtrip=args.real_roundtrip,
            roundtrip_dry_run=args.dry_run,
            db_path=args.db,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
