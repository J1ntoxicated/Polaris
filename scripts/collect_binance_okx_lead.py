"""Binance vs OKX BTC trade lead-time collector — read-only (no trading).

Codex Round 3 spec (INSIGHT-022, 84% 합의):
- 별도 collector script (runner inline 오염 방지)
- Stream: `@trade` raw (microsecond ts) — aggTrade 기각 (aggregation noise)
- 측정: Binance trade ts vs OKX 같은 가격 도달 ts (sample 100+)
- 트리거: HYPO-013 7일 측정 후 + post-fee EV 양수 확인 시 → BinanceLeadSignal 구현 진행

Usage:
    python -m scripts.collect_binance_okx_lead --duration 3600 --output data/lead_pairs.jsonl

Output JSONL:
    {"ts_binance": 1777..., "ts_okx": 1777..., "lead_ms": 234, "price": 79123.4, "side": "buy"}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import deque
from pathlib import Path

import websockets

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
INST = "BTC-USDT"
BINANCE_SYMBOL = "btcusdt"

# 매칭 buffer — Binance trade의 같은 price가 OKX에서 N초 안 도달하면 pair
MATCH_WINDOW_S = 5.0
PRICE_TOLERANCE_BPS = 1.0  # 0.01% — same price 판정


async def stream_binance(buffer: deque) -> None:
    """Binance @trade — push (ts, price, side) to buffer."""
    while True:
        try:
            async with websockets.connect(BINANCE_WS_URL, ping_interval=180, ping_timeout=600) as ws:
                sub = {"method": "SUBSCRIBE", "params": [f"{BINANCE_SYMBOL}@trade"], "id": 1}
                await ws.send(json.dumps(sub))
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("e") != "trade":
                        continue
                    buffer.append({
                        "ts_ms": int(msg["T"]),  # trade time
                        "price": float(msg["p"]),
                        "side": "sell" if msg["m"] else "buy",  # m=True = buyer is maker = sell aggressor
                    })
        except Exception as e:
            print(f"[binance] reconnect after error: {e}")
            await asyncio.sleep(5)


async def stream_okx(buffer: deque) -> None:
    """OKX trades — push (ts, price, side) to buffer."""
    while True:
        try:
            async with websockets.connect(OKX_WS_URL, ping_interval=20, ping_timeout=15) as ws:
                sub = {"op": "subscribe", "args": [{"channel": "trades", "instId": INST}]}
                await ws.send(json.dumps(sub))
                async for raw in ws:
                    msg = json.loads(raw)
                    data = msg.get("data", [])
                    for t in data:
                        buffer.append({
                            "ts_ms": int(t["ts"]),
                            "price": float(t["px"]),
                            "side": t["side"],
                        })
        except Exception as e:
            print(f"[okx] reconnect after error: {e}")
            await asyncio.sleep(5)


def match_lead(binance_buf: deque, okx_buf: deque, output_file) -> int:
    """Binance trade가 OKX 같은 price에 도달한 시간 측정. Return n_matched."""
    matched = 0
    while binance_buf:
        bt = binance_buf[0]
        # Look for matching OKX trade within window
        cutoff = bt["ts_ms"] + int(MATCH_WINDOW_S * 1000)
        match = None
        for ot in list(okx_buf):
            if ot["ts_ms"] < bt["ts_ms"]:
                continue
            if ot["ts_ms"] > cutoff:
                break
            tol = bt["price"] * PRICE_TOLERANCE_BPS / 10000
            if abs(ot["price"] - bt["price"]) <= tol and ot["side"] == bt["side"]:
                match = ot
                break
        if match:
            output_file.write(json.dumps({
                "ts_binance": bt["ts_ms"],
                "ts_okx": match["ts_ms"],
                "lead_ms": match["ts_ms"] - bt["ts_ms"],
                "price": bt["price"],
                "side": bt["side"],
            }) + "\n")
            output_file.flush()
            matched += 1
        # Remove processed Binance trade (matched or not)
        binance_buf.popleft()
        # Trim okx_buf to last 60s
        cutoff_old = bt["ts_ms"] - 60_000
        while okx_buf and okx_buf[0]["ts_ms"] < cutoff_old:
            okx_buf.popleft()
    return matched


async def main(duration_s: int, output_path: Path) -> None:
    binance_buf: deque = deque(maxlen=1000)
    okx_buf: deque = deque(maxlen=1000)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("a") as out:
        # Background streamers
        b_task = asyncio.create_task(stream_binance(binance_buf))
        o_task = asyncio.create_task(stream_okx(okx_buf))

        start = time.time()
        total_matched = 0
        try:
            while time.time() - start < duration_s:
                await asyncio.sleep(1)
                # Wait for a few seconds of buffer before matching (give okx a chance to arrive)
                if binance_buf and (time.time() * 1000 - binance_buf[0]["ts_ms"]) > MATCH_WINDOW_S * 1000:
                    n = match_lead(binance_buf, okx_buf, out)
                    if n > 0:
                        total_matched += n
                        elapsed = time.time() - start
                        print(f"[{elapsed:.0f}s] matched={total_matched} binance_buf={len(binance_buf)} okx_buf={len(okx_buf)}")
        finally:
            b_task.cancel()
            o_task.cancel()
            try:
                await b_task
            except asyncio.CancelledError:
                pass
            try:
                await o_task
            except asyncio.CancelledError:
                pass

        print(f"\nDONE. total_matched={total_matched} written to {output_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Collect Binance vs OKX BTC trade lead-time pairs")
    ap.add_argument("--duration", type=int, default=3600, help="seconds (default 1h)")
    ap.add_argument("--output", type=Path,
                    default=Path("data/binance_okx_lead_pairs.jsonl"))
    args = ap.parse_args()
    asyncio.run(main(args.duration, args.output))
