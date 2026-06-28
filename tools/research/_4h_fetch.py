"""Fetch native OKX 4H + 1D history-candles for the 4h-trend backtest.

DEMO/PAPER research. us.okx.com. Aggressive bias preserved (flow_not_block:
this is edge VALIDATION, not a defensive block). No rejection-keyword logic.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

BASE = "https://us.okx.com/api/v5/market/history-candles"

# Liquid OKX USDT majors (recognizable, real notional) + a few large alts.
# Validated to exist on us.okx.com (delisted/renamed ones removed: MATIC/RNDR/TON).
SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT", "DOGE-USDT",
    "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT", "LTC-USDT", "BCH-USDT",
    "TRX-USDT", "UNI-USDT", "AAVE-USDT", "ATOM-USDT", "ETC-USDT",
    "FIL-USDT", "NEAR-USDT", "OP-USDT", "ARB-USDT", "SUI-USDT", "INJ-USDT",
    "APT-USDT", "SEI-USDT", "WLD-USDT", "TIA-USDT", "PEPE-USDT", "SHIB-USDT",
    "HBAR-USDT",
]

MONTHS_BACK = 6
NOW_MS = int(time.time() * 1000)
START_MS = NOW_MS - MONTHS_BACK * 30 * 86400 * 1000


HDRS = {"User-Agent": "Mozilla/5.0 polaris-research"}


def _get(url: str) -> dict:
    backoff = 0.5
    for _ in range(8):
        try:
            req = urllib.request.Request(url, headers=HDRS)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                time.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
                continue
            raise
        except Exception:  # noqa: BLE001
            time.sleep(backoff)
            backoff = min(backoff * 2, 8.0)
    return {"data": []}


def fetch(inst: str, bar: str) -> list[list[str]]:
    out: list[list[str]] = []
    after = NOW_MS
    last_oldest = None
    for _ in range(120):  # hard cap: 120*100 = 12k bars max
        url = f"{BASE}?instId={inst}&bar={bar}&limit=100&after={after}"
        payload = _get(url)
        rows = payload.get("data", [])
        if not rows:
            break
        out.extend(rows)
        oldest = int(rows[-1][0])
        if last_oldest is not None and oldest >= last_oldest:
            break  # no progress -> pagination floor reached
        last_oldest = oldest
        after = oldest
        if oldest <= START_MS:
            break
        time.sleep(0.35)  # ~3 req/s, well under burst limit
    # dedup + filter to window
    seen: dict[int, list[str]] = {}
    for row in out:
        ts = int(row[0])
        if ts >= START_MS:
            seen[ts] = row
    return [seen[k] for k in sorted(seen)]


def main() -> None:
    db = sqlite3.connect("data/research_4h.sqlite")
    db.execute(
        "CREATE TABLE IF NOT EXISTS c ("
        "symbol TEXT, bar TEXT, ts INTEGER, o REAL, h REAL, l REAL, "
        "cl REAL, vol REAL, notional REAL, PRIMARY KEY(symbol,bar,ts))"
    )
    for inst in SYMBOLS:
        for bar in ("4H", "1D"):
            rows = fetch(inst, bar)
            n = 0
            for row in rows:
                # OKX: [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]
                if row[8] != "1":  # only confirmed (closed) bars
                    continue
                db.execute(
                    "INSERT OR REPLACE INTO c VALUES (?,?,?,?,?,?,?,?,?)",
                    (inst, bar, int(row[0]), float(row[1]), float(row[2]),
                     float(row[3]), float(row[4]), float(row[5]),
                     float(row[7])),
                )
                n += 1
            db.commit()
            print(f"{inst} {bar}: {n} bars")
    db.close()


if __name__ == "__main__":
    main()
