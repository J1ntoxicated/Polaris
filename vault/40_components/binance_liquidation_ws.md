---
title: binance_liquidation_ws
pure: false
code_path: src/data/binance_liquidation_ws.py
test_path: tests/data/test_binance_liquidation_ws.py
hypo: HYPOTHESIS-023
phase: Phase 2k (2026-05-04)
status: active
tags: [shell, websocket, liquidation, binance-perp, event-driven]
entity_type: component
entity_id: binance_liquidation_ws
expires: never
editable: true
last_modified: 2026-05-04
reviewed_by: codex
auto: false
mode: dev
back_links: ["[[INSIGHT-033]]", "[[HYPO-023]]"]
---

# binance_liquidation_ws

Binance Perp Liquidation WebSocket shell module.

## Purpose

Consume Binance `{symbol}@forceOrder` stream (public, no auth) and maintain
in-memory liquidation pressure buffer per symbol.

Data source only — OKX SPOT executes via `src/paper/realtime_runner.py`.

## Key Functions

| Function | Pure | Description |
|---|---|---|
| `_handle_liquidation(msg)` | shell | forceOrder event 파싱 + `_liquidation_store` 업데이트 |
| `get_recent_liquidations(symbol, lookback_ms)` | pure-read | 최근 N ms 청산 list |
| `compute_liquidation_pressure(symbol, lookback_ms)` | pure-read | total/long/short/imbalance/count 집계 |
| `stream(symbols, on_event, reconnect_delay)` | shell | async WS 루프 + reconnect |

## Side Convention

- `S="SELL"` → long position 강제 종료 (long 청산) → `"sell"` stored
- `S="BUY"` → short position 강제 종료 (short 청산) → `"buy"` stored

## Filters

- `value_usd < 1000` 노이즈 필터 (경계 포함: >= 1000 저장)
- non-USDT symbol 무시

## WS Endpoint

`wss://fstream.binance.com/ws` (Binance Futures public stream)

## Links

- [[HYPOTHESIS-023]]
- [[liquidation_cascade]] (consumer strategy)
- [[binance_ws]] (SPOT WS 패턴 참조)
