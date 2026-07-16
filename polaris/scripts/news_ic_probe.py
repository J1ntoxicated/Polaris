"""News sentiment Information Coefficient probe (frontgate-scan item #7,
stage 2, offline).

DEMO/PAPER, virtual capital. Read-only offline digest — joins
``news_timing_shadow`` (item #7's per-article shadow log) against 1H
``bars`` by ``(symbol, ingestion_ts)`` to compute a forward-return sample,
then the Pearson correlation between ``sentiment`` and that forward return
(the roadmap's "IC", item #7: promote once IC > 0.02 with n >= 300, ingestion
timestamp basis). Mirrors ``polaris.core.probes.gate_kill_value``'s
read-only digest shape (``SELECT`` only, no INSERT/UPDATE/DELETE, degrades to
``{"present": False}`` rather than raising) and ``gate_kill_value.py``'s
symbol+timestamp forward-return join style.

NEVER read by any live gate/sizing/exit path — evidence for a /debate-gated
promotion decision only.

Usage:
  python3 -m polaris.scripts.news_ic_probe --db data/polaris_live.sqlite
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
from typing import Any, Final

__all__ = ["DEFAULT_HORIZON_SEC", "DEFAULT_MIN_N", "compute_news_ic"]

# Forward-return horizon: 24h (roadmap item #7 default; ingestion-ts basis).
DEFAULT_HORIZON_SEC: Final[int] = 86_400
DEFAULT_MIN_N: Final[int] = 300


def _forward_return(
    conn: sqlite3.Connection, *, symbol: str, ingestion_ts: int, horizon_sec: int
) -> float | None:
    """1H-bar forward return: (close at/after ingestion+horizon) / (close
    at/after ingestion) - 1. ``None`` when either mark is missing."""
    entry = conn.execute(
        "SELECT close FROM bars WHERE symbol = ? AND bar_interval = '1H' "
        "AND ts >= ? ORDER BY ts ASC LIMIT 1",
        (symbol, ingestion_ts),
    ).fetchone()
    if entry is None or float(entry[0]) <= 0.0:
        return None
    forward = conn.execute(
        "SELECT close FROM bars WHERE symbol = ? AND bar_interval = '1H' "
        "AND ts >= ? ORDER BY ts ASC LIMIT 1",
        (symbol, ingestion_ts + horizon_sec),
    ).fetchone()
    if forward is None:
        return None
    return (float(forward[0]) - float(entry[0])) / float(entry[0])


def compute_news_ic(
    conn: sqlite3.Connection,
    *,
    horizon_sec: int = DEFAULT_HORIZON_SEC,
    min_n: int = DEFAULT_MIN_N,
) -> dict[str, Any]:
    """Read-only digest: Pearson IC between ``sentiment`` and forward return.

    Returns ``{"present": False}`` when fewer than ``min_n`` (sentiment,
    forward_return) pairs resolve (missing table, empty table, or an
    insufficient sample) — mirrors ``gate_kill_value.gate_kill_value_panel``'s
    degrade shape. Fail-open: any ``sqlite3.Error`` on the read yields
    ``{"present": False}`` too.
    """
    try:
        rows = conn.execute(
            "SELECT symbol, ingestion_ts, sentiment FROM news_timing_shadow"
        ).fetchall()
    except sqlite3.Error:
        return {"present": False}
    if not rows:
        return {"present": False}

    sentiments: list[float] = []
    returns: list[float] = []
    for symbol, ingestion_ts, sentiment in rows:
        fwd = _forward_return(
            conn, symbol=str(symbol), ingestion_ts=int(ingestion_ts), horizon_sec=horizon_sec,
        )
        if fwd is None:
            continue
        sentiments.append(float(sentiment))
        returns.append(fwd)

    n = len(sentiments)
    if n < min_n:
        return {"present": False, "n": n, "min_n": min_n}
    try:
        ic = statistics.correlation(sentiments, returns)
    except statistics.StatisticsError:
        return {"present": False, "n": n, "min_n": min_n}
    return {
        "present": True,
        "n": n,
        "ic": ic,
        "horizon_sec": horizon_sec,
        "auto_apply": False,
        "debate_gated": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Trading SQLite path (must exist)")
    parser.add_argument("--horizon-sec", type=int, default=DEFAULT_HORIZON_SEC)
    parser.add_argument("--min-n", type=int, default=DEFAULT_MIN_N)
    args = parser.parse_args()
    # storage-split (round 4 fix): news_timing_shadow + bars are BOTH
    # marketdata-domain — read the sibling, not the (post-split, always-
    # empty) trading db_path passed via --db.
    from polaris.storage.schema_marketdata import marketdata_db_path_for

    md_path = marketdata_db_path_for(args.db)
    conn = sqlite3.connect(f"file:{md_path}?mode=ro", uri=True)
    try:
        digest = compute_news_ic(conn, horizon_sec=args.horizon_sec, min_n=args.min_n)
        print(digest)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
