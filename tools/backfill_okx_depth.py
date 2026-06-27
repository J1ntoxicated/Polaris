"""③ One-shot deep 1D (or 4H) backfill for the top-N OKX-by-volume symbols.

DEMO/PAPER only. flow_not_block + storm-safe (#21): SEQUENTIAL over symbols, each
page paced through the shared ``_CANDLES_BUCKET`` + an inter-page pause, so the
whole job runs at ~0.7 req/s — far under the 10 req/s OKX cap. Run it ONCE, when
the bot is quiet; it is NOT part of the live tick loop.

The deep canvas is for FUTURE indicator depth / learning — there is no current
consumer that reads > ~260 bars, so this never changes any live decision. The
retention ``bars`` window was widened to ~3.3y so the backfill is not pruned.

Usage
-----
    python3 -m tools.backfill_okx_depth --db data/polaris_live.sqlite \
        --interval 1D --top-n 30 --target-bars 1000
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3

from polaris.core.data.ingest import persist_bars
from polaris.venues.okx.adapter import fetch_okx_history_bars

logger = logging.getLogger("backfill_okx_depth")


def _top_okx_symbols(conn: sqlite3.Connection, top_n: int) -> list[str]:
    """Active OKX symbols ranked by 24h USD volume (highest first), capped at N.

    Reads the live universe table; the deep backfill targets only the most-liquid
    majors (the deep daily canvas matters most where OKX history is reliable)."""
    rows = conn.execute(
        """
        SELECT symbol FROM universe
        WHERE venue = 'okx' AND is_active = 1
        ORDER BY vol_24h_usd DESC
        LIMIT ?
        """,
        (int(top_n),),
    ).fetchall()
    return [str(r[0]) for r in rows]


async def backfill(
    db_path: str, *, interval: str, top_n: int, target_bars: int
) -> dict[str, int]:
    """Run the deep backfill; returns ``{symbol: bars_persisted}``."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        symbols = _top_okx_symbols(conn, top_n)
        if not symbols:
            logger.warning("[backfill] no active OKX symbols in universe — nothing to do")
            return {}
        logger.info(
            "[backfill] %d OKX symbols, interval=%s target=%d bars each",
            len(symbols), interval, target_bars,
        )
        out: dict[str, int] = {}
        for sym in symbols:
            bars = await fetch_okx_history_bars(
                sym, bar_interval=interval, target_bars=target_bars,
            )
            if bars:
                n = persist_bars(conn, bars)
                conn.commit()
                out[sym] = n
                logger.info("[backfill] %s/%s persisted=%d", sym, interval, n)
            else:
                out[sym] = 0
                logger.info("[backfill] %s/%s no history returned", sym, interval)
        return out
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Deep OKX bar backfill (paced).")
    parser.add_argument("--db", required=True, help="path to the live sqlite DB")
    parser.add_argument("--interval", default="1D", help="bar interval (1D / 4H)")
    parser.add_argument("--top-n", type=int, default=30, help="top-N OKX-by-volume")
    parser.add_argument(
        "--target-bars", type=int, default=1000, help="bars to backfill per symbol"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    result = asyncio.run(
        backfill(
            args.db, interval=args.interval, top_n=args.top_n,
            target_bars=args.target_bars,
        )
    )
    total = sum(result.values())
    logger.info("[backfill] DONE — %d symbols, %d bars persisted", len(result), total)


if __name__ == "__main__":
    main()
