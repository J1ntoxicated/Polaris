"""Binance USDⓈ-M Futures **testnet** public market data — klines → canonical Bar.

No auth. Split out of ``adapter.py`` to keep that file under the 500-LOC cap.
"""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx

from polaris.core.data.canonical import compute_underlying_group_id
from polaris.core.data.schema import Bar

logger = logging.getLogger(__name__)

__all__ = [
    "BINANCE_BOOK_TICKER_PATH",
    "BINANCE_KLINES_PATH",
    "BINANCE_TESTNET_FUTURES_BASE",
    "binance_kline_to_bar",
    "fetch_binance_bars",
]

BINANCE_TESTNET_FUTURES_BASE: Final[str] = "https://testnet.binancefuture.com"
BINANCE_KLINES_PATH: Final[str] = "/fapi/v1/klines"
BINANCE_BOOK_TICKER_PATH: Final[str] = "/fapi/v1/ticker/bookTicker"
REST_TIMEOUT_SEC: Final[float] = 15.0

# Binance kline `interval` strings keyed by canonical bar_interval.
_INTERVAL_MAP: Final[dict[str, str]] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def binance_kline_to_bar(
    kline: list[Any],
    *,
    symbol: str,
    bar_interval: str,
    underlying_group_id: str,
) -> Bar:
    """Convert one Binance ``/fapi/v1/klines`` row to a canonical Bar.

    Kline row layout:
    ``[openTime, open, high, low, close, volume, closeTime, quoteVol,
       trades, takerBuyBase, takerBuyQuote, ignore]``.
    """
    if len(kline) < 9:
        raise ValueError(f"Binance kline row too short: {kline!r}")
    ts = int(kline[0]) // 1000  # ms epoch → seconds
    return Bar(
        instrument_id=f"binance:{symbol}",
        underlying_group_id=underlying_group_id,
        venue="binance",
        symbol=symbol,
        bar_interval=bar_interval,
        ts=ts,
        open=_safe_float(kline[1]),
        high=_safe_float(kline[2]),
        low=_safe_float(kline[3]),
        close=_safe_float(kline[4]),
        volume=_safe_float(kline[5]),
        notional_usd=_safe_float(kline[7]),
        trade_count=int(kline[8]) if str(kline[8]).isdigit() else 0,
        vwap=0.0,
        bid_close=0.0,
        ask_close=0.0,
        spread_bps_close=0.0,
        source="binance_rest",
    )


async def fetch_binance_bars(
    symbol: str,
    *,
    bar_interval: str = "1m",
    limit: int = 300,
    base_url: str = BINANCE_TESTNET_FUTURES_BASE,
    client: httpx.AsyncClient | None = None,
) -> list[Bar]:
    """Fetch up to ``limit`` ``bar_interval`` klines from Binance futures (no auth)."""
    interval = _INTERVAL_MAP.get(bar_interval)
    if interval is None:
        raise ValueError(f"unsupported bar_interval={bar_interval!r}")
    own = client is None
    cli = client or httpx.AsyncClient(base_url=base_url, timeout=REST_TIMEOUT_SEC)
    try:
        resp = await cli.get(
            BINANCE_KLINES_PATH,
            params={"symbol": symbol, "interval": interval, "limit": str(limit)},
        )
        resp.raise_for_status()
        rows = resp.json()
    finally:
        if own:
            await cli.aclose()
    if not isinstance(rows, list):
        raise RuntimeError(f"Binance klines unexpected payload: {type(rows).__name__}")
    underlying = compute_underlying_group_id("binance", symbol, asset_class="crypto")
    out: list[Bar] = []
    for row in rows:
        try:
            out.append(
                binance_kline_to_bar(
                    row,
                    symbol=symbol,
                    bar_interval=bar_interval,
                    underlying_group_id=underlying,
                )
            )
        except (ValueError, IndexError, TypeError):
            continue
    logger.debug(
        "[binance] bars fetched %s/%s requested=%d got=%d",
        symbol,
        bar_interval,
        limit,
        len(out),
    )
    return out
