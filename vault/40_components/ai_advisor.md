---
title: ai_advisor
pure: false
code_path: src/strategies/ai_advisor.py
test_path: tests/strategies/test_ai_advisor.py
hypo: HYPO-AI-001
phase: Phase 3 (2026-05-04)
status: active
tags: [shell, strategy, ai, claude, realtime-analysis, multi-source]
entity_type: component
entity_id: ai_advisor
expires: never
editable: true
last_modified: 2026-05-04
reviewed_by: codex(1 round)
auto: false
mode: dev
back_links: ["[[INSIGHT-034]]", "[[HYPO-AI-001]]"]
---

# ai_advisor

Claude AI Advisor Strategy — shell (P6, I/O: Anthropic API).

Jin mandate: '원래 의도는 AI 개입 실시간 분석으로 거래'.
모태 (auto_invasion_mk1) advisor 핵심 패턴 인수 → Polaris SPOT 전용 재설계.

## Pure Core

- `_build_prompt(market_state) -> str` — deterministic prompt builder
- `_parse_response(text) -> dict` — JSON parse with fallback (never raises)
- `_state_hash(market_state) -> str` — MD5 cache key
- `_estimate_cost(input_tokens, output_tokens) -> float` — cost estimation
- `AIAdvisor._response_to_signal(response, ts_ms) -> Signal` — dict → Signal

## Shell Boundary

- `AIAdvisor.evaluate_ai(market_state) -> Signal` — Anthropic API call + cache write
- `_track_call(input_tokens, output_tokens)` — hourly cost logging
- Rate limit: 1 API call per ticker per 60s
- Cache: same market_state hash → 5min TTL (cache before rate limit check)

## HYPO-AI-001 Spec

| Field | Value |
|-------|-------|
| Model | claude-haiku-4-5-20251001 |
| Tickers | BTC-USDT, ETH-USDT, SOL-USDT |
| Min confidence | 0.65 |
| Target size | $200 USD |
| Exit profile | liquidation (TP 1.5%, SL 0.7%, max 30min) |
| Rate limit | 1 call / ticker / 60s |
| Cache TTL | 300s (5min) |

## Cost Profile

- Per call: ~500 input + 50 output tokens = ~$0.0006
- 3 tickers × 60 calls/h = 180 calls/h → ~$0.11/h
- Daily: ~$2.6 (profitable if ≥ 1 win/day covers cost)

## Market State Assembly (Shell — realtime_runner)

Sources combined per tick:
- OKX WS: last price, change_24h, taker_buy_ratio, book_imbalance
- OKX trades WS: VPIN approximation (50-trade window)
- Candle cache (1H): RSI approximation (14-period)
- BTC 1D cache: trend_1d, trend_4h (proxy), regime
- Funding rate cache: funding_8h (Binance Futures, 60s poll)

## Decision Logic

```
action=long + confidence >= 0.65  → ENTER_LONG ($200)
action=exit                        → EXIT
action=hold | low confidence       → HOLD
API error                          → HOLD (safe fallback)
Rate limited (< 60s)               → HOLD (no API call)
Cache hit (< 5min)                 → cached Signal (no API call)
```

## Log Pattern

```
[AI-DECISION] BTC-USDT → LONG conf=0.78 reason='strong momentum + low funding'
[AI-COST] 1h window: 180 calls (180.0/h) est $0.108/h | cumulative $0.432 (720 calls)
[AI-HOLD] ETH-USDT uncertain_regime
```
