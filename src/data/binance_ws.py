"""Binance WebSocket public stream — shell (P6).

Codex DEBATE Round 3 spec (INSIGHT-022, 84% 합의):
- Single connection + multi SUBSCRIBE (1024 streams/connection 최대, 8 ticker × 2 stream = 16)
- Stream: `@trade` (raw, microsecond ts) + `@bookTicker` (best bid/ask)
- `@aggTrade` 기각 (aggregation noise → lead time 측정 부적합)
- 24h 자동 종료 → reconnect 의무
- Public stream auth 불필요
- Limit: 300 connections/5min/IP, 5 incoming msgs/s

Module-level cache (in-memory, single process):
- `_trade_store: dict[symbol, deque(ts_ms, side, size, price)]` — 최근 100 trades per symbol
- `_book_ticker_store: dict[symbol, dict(bid, ask, ts_ms)]` — 마지막 best bid/ask

Reconnect: exponential backoff 5s → 60s + stale cache clear (cross-exchange microstructure 일관성).

REFERENCE: src/data/okx_ws.py 패턴.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Callable, Optional

try:
    import websockets
except ImportError:
    websockets = None

logger = logging.getLogger(__name__)

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"

# Binance SPOT whitelist — only major pairs confirmed listed on Binance SPOT.
# TRUMP/ORDI etc. are niche / exchange-specific → silent fail without this guard.
# Fix 3 (Codex Round 4): prevent silent subscription to unsupported streams.
BINANCE_SPOT_SYMBOLS: frozenset[str] = frozenset({
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "BNBUSDT", "ADAUSDT",
    "XRPUSDT", "PEPEUSDT", "SUIUSDT", "AVAXUSDT", "LINKUSDT", "MATICUSDT",
    "DOTUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT", "AAVEUSDT", "UNIUSDT",
    "SHIBUSDT", "FLOKIUSDT",
    # NOTE: TRUMP, ORDI intentionally excluded — not on Binance major SPOT
})

# Module-level state (single-process)
_trade_store: dict[str, deque] = {}        # symbol("BTCUSDT") -> deque[(ts_ms, side, size, price)]
_book_ticker_store: dict[str, dict] = {}   # symbol -> {bid, ask, ts_ms}
_TRADE_BUFFER_SIZE = 200


# ── Public read accessors (caller-safe) ──────────────────────────────────────


def get_last_trade(symbol: str) -> Optional[tuple]:
    """Last trade tuple (ts_ms, side, size, price) for symbol (e.g. 'BTCUSDT')."""
    buf = _trade_store.get(symbol.upper())
    if not buf:
        return None
    return buf[-1]


def get_recent_trades(symbol: str, n: int = 100) -> list:
    buf = _trade_store.get(symbol.upper())
    if not buf:
        return []
    return list(buf)[-n:]


def get_book_ticker(symbol: str) -> Optional[dict]:
    return _book_ticker_store.get(symbol.upper())


def compute_taker_buy_ratio(symbol: str, window: int = 100) -> Optional[float]:
    """Binance trade aggressor side ratio: buy_size / total_size on recent N trades.

    Pure (P6) computation on cached data.
    """
    trades = get_recent_trades(symbol, window)
    if not trades:
        return None
    buy_size = sum(t[2] for t in trades if t[1] == "buy")
    total = sum(t[2] for t in trades)
    if total <= 0:
        return None
    return buy_size / total


def compute_recent_volatility_bps(symbol: str, window: int = 50) -> Optional[float]:
    """Recent price volatility (bps) — std-like measure of last N trade prices."""
    trades = get_recent_trades(symbol, window)
    if len(trades) < 2:
        return None
    prices = [t[3] for t in trades]
    avg = sum(prices) / len(prices)
    dev = max(abs(p - avg) for p in prices) / avg * 10000
    return dev


# ── Frame handlers (pure parsing) ───────────────────────────────────────────


def _handle_trade(msg: dict) -> None:
    """Binance @trade event:
    {e: "trade", T: ts_ms, s: "BTCUSDT", p: price, q: qty, m: bool (true=buyer is maker = sell aggr)}
    """
    sym = msg.get("s", "").upper()
    if not sym:
        return
    try:
        ts_ms = int(msg["T"])
        price = float(msg["p"])
        size = float(msg["q"])
        # m=True: buyer is maker → this trade was initiated by SELL aggressor
        # m=False: buyer is taker → BUY aggressor
        side = "sell" if msg.get("m") else "buy"
    except (KeyError, ValueError, TypeError):
        return
    buf = _trade_store.setdefault(sym, deque(maxlen=_TRADE_BUFFER_SIZE))
    buf.append((ts_ms, side, size, price))


def _handle_book_ticker(msg: dict) -> None:
    """Binance @bookTicker event:
    {e: "bookTicker", s: "BTCUSDT", b: bid, B: bidQty, a: ask, A: askQty}
    또는 raw: {u: updateId, s, b, B, a, A} (no 'e' field for bookTicker)
    """
    sym = msg.get("s", "").upper()
    if not sym:
        return
    try:
        bid = float(msg["b"])
        ask = float(msg["a"])
    except (KeyError, ValueError, TypeError):
        return
    _book_ticker_store[sym] = {
        "bid": bid,
        "ask": ask,
        "ts_ms": int(time.time() * 1000),  # bookTicker has no T field, use receive ts
    }


# ── Subscription + main loop ────────────────────────────────────────────────


def _build_streams(symbols: list[str]) -> list[str]:
    """e.g. ['BTC-USDT', 'ETH-USDT'] → ['btcusdt@trade', 'btcusdt@bookTicker', ...]

    Fix 3 (Codex Round 4): symbols not in BINANCE_SPOT_SYMBOLS are skipped with a
    warning — prevents silent fail where WS connection persists but stream is ignored.
    """
    streams = []
    for s in symbols:
        sym = s.replace("-", "").upper()
        if sym not in BINANCE_SPOT_SYMBOLS:
            logger.warning(f"Binance unsupported symbol skipped: {s} ({sym})")
            continue
        sym_lower = sym.lower()
        streams.append(f"{sym_lower}@trade")
        streams.append(f"{sym_lower}@bookTicker")
    return streams


async def _subscribe(ws, symbols: list[str]) -> None:
    streams = _build_streams(symbols)
    msg = {"method": "SUBSCRIBE", "params": streams, "id": int(time.time())}
    await ws.send(json.dumps(msg))
    logger.info(f"Binance WS subscribed {len(streams)} streams ({len(symbols)} symbols)")


async def _on_message(raw: str, on_event: Optional[Callable[[str, dict], None]]) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    # Subscription ack: {"result": null, "id": ...}
    if "result" in msg and "id" in msg:
        return
    event_type = msg.get("e")
    sym = msg.get("s", "").upper()
    if event_type == "trade":
        _handle_trade(msg)
        if on_event and sym:
            on_event(sym, {"type": "trade", **msg})
    elif event_type == "bookTicker" or ("u" in msg and "b" in msg and "a" in msg):
        # bookTicker raw stream omits 'e' — detect by fields
        _handle_book_ticker(msg)
        if on_event and sym:
            on_event(sym, {"type": "bookTicker", **msg})


async def stream(
    symbols: list[str],
    on_event: Optional[Callable[[str, dict], None]] = None,
    reconnect_delay: float = 5.0,
) -> None:
    """Binance WS public stream — single connection, multi SUBSCRIBE.

    `symbols`: list of OKX-style instruments (e.g. ['BTC-USDT']) — converted to Binance format.
    `on_event(symbol, event_dict)`: optional callback per processed event.
    """
    if websockets is None:
        raise RuntimeError("websockets package not installed")

    backoff = reconnect_delay
    while True:
        try:
            # ping_interval 180s (Binance recommends < 3min); ping_timeout 600 generous
            async with websockets.connect(BINANCE_WS_URL, ping_interval=180, ping_timeout=600) as ws:
                # Reconnect → cross-exchange stale clear (Codex Round 3 carryover from OKX fix)
                _trade_store.clear()
                _book_ticker_store.clear()
                logger.info("Binance stale store cleared on reconnect")
                await _subscribe(ws, symbols)
                backoff = reconnect_delay
                async for raw in ws:
                    await _on_message(raw, on_event)
        except Exception as e:
            logger.error(f"Binance WS error: {e}, reconnect in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
