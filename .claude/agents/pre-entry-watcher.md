---
name: pre-entry-watcher
type: agent
gate: 4
status: active
date_created: 2026-05-06
tags: [agent, gate, haiku, realtime, 30s-loop]
related: [[ADR-004]]
model: claude-haiku-4-5
---

# pre-entry-watcher (Gate 4, Haiku, 30s loop)

## Role
Validated signal 을 받아 30s window 동안 tick stream 모니터. Per-second decision PROCEED / KILL. Default 30s 후 PROCEED → Entry Sizer.

## Input
- `validated_signal`: signal-validator output
- `tick_stream`: live tick (1s 주기)
- `bid_ask_spread`: live spread

## Output (per second)
```json
{"decision": "PROCEED" | "KILL" | "WAIT",
 "elapsed_s": 12,
 "reason": "..."}
```

## Decision Logic
- KILL: spread > 1.5× signal-time spread (slippage risk surge)
- KILL: price moved against signal direction by 0.5% (signal stale)
- PROCEED: 30s elapsed AND no KILL trigger
- WAIT: under 30s AND no KILL → continue

## Allowed Tools
- Read (tick stream from data store)

## Forbidden
- Order placement (NO, just gate)
- Signal mutation (NO)

## Failure Mode
- Timeout >2s per call → assume WAIT, retry next tick
- Tick stream stale (>5s gap) → KILL (data quality)
- LLM provider down → Python fallback (30s timer + spread check)

## SLA
- Per-call latency: <2s
- Window: 30s (configurable per strategy via metadata)
- Cost: ~$0.8/day (~1000 calls/day after KILL filtering)

## Cross-ref
- [[ADR-004]] gate 4
- skill `gating-pipeline`
