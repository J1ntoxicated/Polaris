---
name: post-trade-reflector
type: agent
gate: 8
status: active
date_created: 2026-05-06
tags: [agent, gate, opus-p1, python-p0, learning]
related: [[ADR-004]], [[ADR-006]], [[ADR-007]]
model: claude-opus-4-7  # P1; P0 = Python lesson template
---

# post-trade-reflector (Gate 8, Opus P1 / Python template P0)

## Role
Closed trade 마다 호출. lesson + cell_matrix delta + learner adjustment emit. Vault `50_research/lessons/` 에 lesson 기록. ai_feedback learner (#7 [[ADR-007]]) 의 입력 source.

## Input
- `closed_trade`: entry/exit/PnL/strategy/ticker/regime/duration
- `market_context`: trade window 동안 regime/vol/news (있다면)
- `signal_chain`: gate 1-7 decision history (debug 용)

## Output
```json
{
  "lesson_md": "...",            // vault append
  "cell_matrix_delta": {
    "exchange": "okx", "strategy": "volume_burst",
    "ticker": "BTC-USDT", "regime": "bull_trend",
    "n_delta": 1, "pnl_delta": 0.012
  },
  "learner_hints": {
    "session_mult": {"key": "asia:volume_burst", "delta": 0.05},
    "ai_feedback": {"strategy": "volume_burst", "weight_delta": 0.02}
  }
}
```

## Decision Logic
- Lesson template (P0): "Trade {id} {strategy} {ticker} {regime} → PnL {pct}%. Driver: {tag}. Learning: {default-rule}."
- AI lesson (P1): chain-of-thought reasoning, what-if comparison, contradicting prior lesson 검출

## Allowed Tools
- Read (trade history, prior lessons)
- Write (vault/50_research/lessons/<trade_id>.md only)
- Cell matrix update (단, allocator-fenced — atomic txn)

## Forbidden
- Cell matrix score formula 변경 (NO, fixed = avg_pnl × √n / 70)
- Hard cap parameter 변경 (NO)
- Strategy 자체 수정 (NO, ai_feedback weight 만)

## Failure Mode
- Opus timeout >5s → Python lesson template fallback
- LLM contradicts prior lesson → flag for vault-curator review (lint heavy weekly)
- Cell matrix delta 누락 → trade event log 에서 replay

## SLA
- Latency: <5s
- Cost: ~$1.2/day P1
- Volume: ~50 closed trades/day expected

## Cross-ref
- [[ADR-004]] gate 8
- [[ADR-006]] cell matrix update
- [[ADR-007]] ai_feedback learner #7
- skill `gating-pipeline`, `tuning-learners` (P1)
