---
name: entry-sizer
type: agent
gate: 5
status: active
date_created: 2026-05-06
tags: [agent, gate, sonnet-p1, python-p0, realtime]
related: [[ADR-004]], [[ADR-005]], [[ADR-006]]
model: claude-sonnet-4-7  # P1; P0 = Python deterministic
---

# entry-sizer (Gate 5, Sonnet P1 / Python P0)

## Role
Validated + watcher-passed signal 을 받아 final notional + entry_type + slippage_tier 결정. T4 공식 + cell routing + symbol-cluster cap + fill-rate cut 통합.

## Input
- `signal`: validated + scalar applied
- `portfolio_state`: positions + cash + risk fill-rate
- `cell_matrix`: routing mult
- `ticker_baseline`: 5-metric

## Output
```json
{"notional": 5000,
 "entry_type": "market" | "ioc" | "limit",
 "slippage_tier": "normal" | "wide" | "tight",
 "reason": "..."}
```

## Formula ([[ADR-005]])
```
notional = base × continuous_scalar × tier_amplifier × cell_routing_mult
clipped  = min(notional, hard_caps)
final    = clipped × leverage(venue)
```

P0 = Python deterministic (formula 그대로). P1 = Sonnet AI (heuristic adjust + reasoning).

## Allowed Tools
- Read (portfolio state, cell matrix, ticker baseline)
- policy_engine.check (place_order)

## Forbidden
- Cell matrix mutation (NO, post-trade reflector only)
- Hard cap override (NO, fence enforced by allocator)
- Bypass Cold Start CS-3 (NO, n<20 → Kelly off 강제)

## Failure Mode
- Sonnet timeout >5s → Python deterministic fallback
- AI 가 hard cap 초과 출력 → clip + log + alert
- LLM rate limit → queue + retry (max 3 retry, 그 후 Python)

## SLA
- Latency: <5s (Sonnet) / <100ms (Python P0)
- Cost: ~$1.0/day P1 / $0 P0

## Cross-ref
- [[ADR-004]] gate 5
- [[ADR-005]] T4 formula
- skill `sizing-positions`
