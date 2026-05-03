"""OKX history fetcher — shell layer (P6).

I/O wrapper. Pure parsing in `_parse_okx_response`.

API: https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history
- Endpoint: /api/v5/market/history-candles
- Public, no auth needed.
- bar: 1m, 3m, 5m, 15m, 30m, 1H, 2H, 4H, 6H, 12H, 1D, 1W, 1M
- limit: max 100 per request

INSIGHT-011: live URL only here. demo URL은 trading endpoint와 무관 (history는 public).
"""
from __future__ import annotations

import os
import time
from typing import Iterable

import requests

from src.domain.candle import Candle

OKX_HISTORY_URL = "https://www.okx.com/api/v5/market/history-candles"
OKX_REQUEST_TIMEOUT = 10  # seconds
OKX_RPS_DELAY = 0.15  # 10 req/s frozen_params 정합 (INSIGHT-006 okx_api_rps_max=10)


def _parse_okx_response(payload: dict) -> list[Candle]:
    """Parse OKX response → Candle list. Pure function (P6).

    OKX response data format:
    [["ts", "o", "h", "l", "c", "vol", "volCcy", "volCcyQuote", "confirm"], ...]
    Sorted: newest first.
    """
    if payload.get("code") != "0":
        raise RuntimeError(
            f"OKX error code={payload.get('code')} msg={payload.get('msg', '')}"
        )
    rows = payload.get("data", [])
    candles = []
    for row in rows:
        if len(row) < 6:
            continue
        try:
            candles.append(
                Candle(
                    timestamp_ms=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        except (ValueError, IndexError):
            # 잘못된 row skip (P1 — vault/log에 기록 X, caller 책임)
            continue
    # 시간순 정렬 (오래된 먼저)
    candles.sort(key=lambda c: c.timestamp_ms)
    return candles


def fetch_history(
    inst_id: str = "BTC-USDT",
    bar: str = "1H",
    limit: int = 100,
    after_ms: int | None = None,
) -> list[Candle]:
    """OKX history candles fetch (single page, max 100).

    Note: OKX uses `after` for "older than this timestamp" pagination
    (counter-intuitive). `before` is for newer-than (less common).

    Args:
        inst_id: instrument id (e.g. "BTC-USDT" SPOT).
        bar: timeframe (1m, 5m, 1H, 1D 등).
        limit: 1-100.
        after_ms: pagination cursor — return candles older than this timestamp.

    Returns:
        Candle list (시간순, 오래된 먼저).
    """
    params = {"instId": inst_id, "bar": bar, "limit": str(limit)}
    if after_ms is not None:
        params["after"] = str(after_ms)
    response = requests.get(OKX_HISTORY_URL, params=params, timeout=OKX_REQUEST_TIMEOUT)
    response.raise_for_status()
    return _parse_okx_response(response.json())


def fetch_history_paged(
    inst_id: str = "BTC-USDT",
    bar: str = "1H",
    n_candles: int = 500,
) -> list[Candle]:
    """다중 페이지 fetch — older direction (via `after` pagination).

    Rate limit: OKX_RPS_DELAY 사이 sleep.
    """
    all_candles: list[Candle] = []
    cursor: int | None = None
    while len(all_candles) < n_candles:
        page = fetch_history(inst_id=inst_id, bar=bar, limit=100, after_ms=cursor)
        if not page:
            break
        # page는 시간순 (oldest first). 다음 페이지는 가장 오래된 시점 이전.
        cursor = page[0].timestamp_ms
        all_candles = page + all_candles  # 오래된 page를 앞에
        time.sleep(OKX_RPS_DELAY)
        if len(page) < 100:
            break
    # 최신 n_candles 제한 (chronological, oldest first)
    return all_candles[-n_candles:]
