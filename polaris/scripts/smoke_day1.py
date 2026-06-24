"""Smoke test — Phase 0 P0 Day 1 (Layer 0 + Layer 1 wiring).

Loads `.env` (best-effort), fetches OKX SPOT demo + Capital CFD demo via REST,
applies the 4-axis hard filter, builds the dynamic focus watchlist, and
persists everything to `data/polaris.sqlite`.

Run: `python3 -m polaris.scripts.smoke_day1`
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from polaris.core.data.canonical import compute_underlying_group_id
from polaris.core.universe.discovery import (
    apply_active_filters,
    detect_listing_changes,
    fetch_capital_instruments,
    fetch_okx_instruments,
    merge_listing_timestamps,
    persist_universe,
)
from polaris.core.universe.schema import default_thresholds
from polaris.core.universe.watchlist import compute_dynamic_focus, persist_focus
from polaris.storage.schema import init_db

DOTENV_PATH = Path(".env")


def _load_dotenv(path: Path = DOTENV_PATH) -> None:
    """Lightweight .env loader; ignores missing file or malformed lines."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


async def run() -> None:
    _load_dotenv()
    now_ts = int(time.time())

    print("=" * 60)
    print("Polaris P0 Day 1 — Layer 0 + Layer 1 smoke")
    print(f"now_ts = {now_ts}")
    print("=" * 60)

    # 1. OKX SPOT demo fetch
    try:
        okx = await fetch_okx_instruments(now_ts=now_ts)
    except Exception as exc:  # noqa: BLE001 — smoke must surface message
        print(f"[OKX] fetch failed: {exc!r}")
        okx = []
    print(f"[OKX]     instruments fetched      : {len(okx)}")

    # 2. Capital CFD demo fetch (best-effort; CFD weekend-quiet may yield 0)
    try:
        capital = await fetch_capital_instruments(now_ts=now_ts)
    except Exception as exc:  # noqa: BLE001
        print(f"[Capital] fetch failed: {exc!r}")
        capital = []
    print(f"[Capital] instruments fetched      : {len(capital)}")

    raw_universe = okx + capital
    # First-run watchdog wiring: stamp listing_ts on every row so downstream
    # buckets can reason about freshness. On repeat runs the prev cycle's
    # state should be the input here, but P0 reads from db each cycle.
    universe = merge_listing_timestamps(prev=[], curr=raw_universe, now_ts=now_ts)
    print(f"[combined] total instruments        : {len(universe)}")

    # 3. 4-axis hard filter
    thresholds = default_thresholds()
    filtered = apply_active_filters(universe, thresholds)
    print(f"[filter]   after 4-axis hard filter : {len(filtered)}")
    okx_filtered = [i for i in filtered if i.venue == "okx"]
    cap_filtered = [i for i in filtered if i.venue == "capital"]
    print(f"           ├─ OKX                  : {len(okx_filtered)}")
    print(f"           └─ Capital              : {len(cap_filtered)}")

    # 4. Dynamic focus watchlist (STAGE 1: all active watched, tier-graded)
    focus = compute_dynamic_focus(filtered, cycle_ts=now_ts)
    by_tier: dict[str, int] = {}
    for f in focus:
        by_tier[f.tier] = by_tier.get(f.tier, 0) + 1
    print(f"[focus]    watched (all active)     : {len(focus)}")
    print(f"           tiers                   : {by_tier}")
    if focus:
        top = focus[:5]
        print("           top 5 focus rows:")
        for f in top:
            print(
                f"             rank={f.rank:2d} {f.venue:8s} {f.symbol:14s} "
                f"score={f.focus_score:+.3f} bucket={f.bucket}"
            )

    # 5. Persist universe + focus to SQLite
    db_path = Path("data/polaris.sqlite")
    conn = init_db(db_path)
    try:
        persist_universe(
            conn,
            universe,
            is_active_set={i.instrument_id for i in filtered},
            thresholds=thresholds,
        )
        persist_focus(conn, focus)
    finally:
        conn.close()
    print(f"[db]       persisted to            : {db_path}")

    # 6. Listing-change demo (no prior cycle yet → all current = new, all stamped)
    new, _delisted = detect_listing_changes([], universe, now_ts=now_ts)
    print(f"[listing]  new listings (vs empty) : {len(new)} (all stamped listing_ts={now_ts})")

    # 7. Quick canonical sanity
    sample_group = compute_underlying_group_id("okx", "BTC-USDT", "crypto")
    print(f"[canonical] BTC-USDT group_id     : {sample_group}")

    print("=" * 60)
    print("smoke OK")
    print("=" * 60)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
