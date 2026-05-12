---
name: universe-scanner
type: agent
gate: 1
status: active
date_created: 2026-05-06
tags: [agent, gate, haiku, realtime]
related: [[ADR-004]], [[ADR-006]]
model: claude-haiku-4-5
---

# universe-scanner (Gate 1, Haiku)

## Role
Layer 0 의 active universe (~270-320 instruments) 에서 cell_matrix score + ticker baseline 활용하여 cycle 마다 30-ticker focus watchlist 추출. Cost 최적화 핵심 gate.

## Input
- `active_universe`: list of {symbol, venue, vol_24h, last_active}
- `cell_matrix_scores`: dict[(exchange, ticker), score]
- `ticker_baseline`: dict[symbol, {atr, size, signal, volume, pnl_std} percentile]
- `current_regime`: regime tag

## Output
```json
{"focus_watchlist": ["BTC-USDT", "ETH-USDT", ...30 tickers],
 "rationale_brief": "..."}
```

## Decision Logic (Haiku prompt)
- Top 10: cell_matrix score 상위 + 최근 fresh signal
- Mid 15: liquidity tier high + regime fit
- Bottom 5: exploration (n<5 cells, learner-driven)

## Allowed Tools
- Read (vault baseline)
- Internal state query

## Forbidden
- Order placement (NO)
- Strategy decision override (NO)
- Cell matrix mutation (NO, post-trade reflector only)

## Failure Mode
- Timeout >2s → Python fallback (top 30 by vol_24h)
- LLM hallucination (unknown symbol) → strict pydantic validate, drop unknown
- Empty output → previous cycle watchlist 재사용 (cache 5min)

## SLA
- Latency budget: <2s end-to-end
- Refresh: 5min (OKX) / 10min (Capital)
- Cost: ~$0.4/day (Haiku 288 calls)

## Cross-ref
- [[ADR-004]] gate 1
- [[ADR-006]] cell matrix routing input
- skill `discovering-universe`
