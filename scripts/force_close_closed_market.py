"""MSG-112 (Jin) one-shot — force close every position whose underlying
market is currently closed.

Usage (Ops):
    python3 -m scripts.force_close_closed_market [--dry-run]

Behaviour:
    1. Read portfolio_state.json
    2. For each position, evaluate is_market_open(ticker, asset_group)
    3. Closed-market positions:
       - Capital → CapitalComAdapter.close_position(ticker, "FORCE_CLOSE_JIN")
       - OKX     → PaperTrader._close_position(ticker, "FORCE_CLOSE_JIN")
       - Alpaca  → handled by Harness MCP (not this script)
    4. Stamp positions_snapshots.closed_ts = now
    5. Re-emit portfolio_state.json without the closed entries

Dry-run prints the plan but performs no writes.

Crypto/forex (24/7) are never touched by this script even if Capital paper
quotes a stale spread for them.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "data" / "portfolio_state.json"
DB = REPO / "data" / "invasion.sqlite"

EXCLUDE_GROUPS = {"crypto", "forex"}


def load_state() -> dict:
    with open(STATE) as f:
        return json.load(f)


def save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.replace(STATE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan, do not close or write")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO))
    from invasion.utils.market_hours import is_market_open
    from invasion.utils.groups import get_group as authoritative_group

    state = load_state()
    positions = state.get("positions", {})
    plan = []
    for ticker, pos in positions.items():
        stored_group = (pos.get("asset_group") or "").lower()
        # MSG-112: portfolio_state carries stale Lesson-#38-era classifications
        # (Estee Lauder / Global Payments / Cocoa US labelled "forex"). Use
        # groups.py as the authoritative override so closed-market scan
        # reaches the actually-stock tickers buried inside the forex bucket.
        real_group = authoritative_group(ticker).lower()
        if real_group in EXCLUDE_GROUPS:
            # Genuine 24/7 — skip even if the stored group disagrees
            continue
        if is_market_open(ticker, asset_group=real_group):
            continue
        plan.append((ticker, pos.get("exchange", "?"),
                     f"{stored_group}->{real_group}" if stored_group != real_group else real_group,
                     pos.get("direction", "?"),
                     float(pos.get("size_usd", 0))))

    if not plan:
        print("No closed-market positions found.")
        return 0

    print(f"Closed-market positions to force close ({len(plan)}):")
    for t, ex, g, d, sz in plan:
        print(f"  {ex:6s} {g:9s} {d:5s} {t!r:30s} size=${sz:.0f}")

    if args.dry_run:
        print("\n[DRY-RUN] No close calls issued.")
        return 0

    # Live path — instantiate adapters lazily; one shot, no error swallowing.
    from invasion.config.config import Config
    cfg = Config()
    closed_count = 0
    failures = []
    cap_adapter = None
    okx_paper = None

    for ticker, exchange, group, direction, size_usd in plan:
        try:
            if exchange == "cap":
                if cap_adapter is None:
                    from invasion.exchange.capital.client import CapitalComClient
                    from invasion.exchange.capital_adapter import CapitalComAdapter
                    cap_client = CapitalComClient(cfg)
                    cap_adapter = CapitalComAdapter(cap_client, cfg=cfg)
                cap_adapter.close_position(ticker, "FORCE_CLOSE_JIN")
                closed_count += 1
            elif exchange == "okx":
                if okx_paper is None:
                    from invasion.exchange.okx.paper import PaperTrader
                    okx_paper = PaperTrader(cfg)
                okx_paper._close_position(ticker, "FORCE_CLOSE_JIN")
                closed_count += 1
            else:
                failures.append((ticker, f"unhandled exchange {exchange!r} (Alpaca → Harness MCP)"))
        except Exception as e:
            failures.append((ticker, str(e)))

    print(f"\nForce-closed: {closed_count}")
    if failures:
        print("Failures:")
        for t, msg in failures:
            print(f"  {t}: {msg}")

    # Stamp positions_snapshots and rewrite state file
    closed_tickers = [t for t, _, _, _, _ in plan if t not in {f for f, _ in failures}]
    if closed_tickers and not args.dry_run:
        import sqlite3
        with sqlite3.connect(str(DB)) as conn:
            placeholders = ",".join("?" * len(closed_tickers))
            conn.execute(
                f"UPDATE positions_snapshots SET closed_ts = ? "
                f"WHERE closed_ts IS NULL AND ticker IN ({placeholders})",
                [time.time(), *closed_tickers],
            )
            conn.commit()
        for t in closed_tickers:
            positions.pop(t, None)
        state["positions"] = positions
        state["updated"] = time.time()
        save_state(state)
        print(f"State + positions_snapshots updated for {len(closed_tickers)} tickers.")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
