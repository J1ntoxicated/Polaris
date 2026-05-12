---
name: adaptive-exit
type: agent
gate: 7
status: active
date_created: 2026-05-06
tags: [agent, gate, sonnet-p1, python-p0, realtime]
related: [[ADR-004]], [[ADR-005]], [[active-autonomous-vision]]
model: claude-sonnet-4-7  # P1; P0 = Python
---

# adaptive-exit (Gate 7, Sonnet P1 / Python P0)

## Role
Position Monitor 가 ADJUST_EXIT / EXIT_NOW emit 시, 또는 default ATR×N exit 도달 시 호출. Default ATR exit 위에 AI override layer. **Winner 길게, default 보호**.

## Input
- `position`: active state
- `regime`: current regime flag
- `recent_volatility`: ATR / realized vol
- `default_exit`: strategy 별 ATR×N exit level

## Output
```json
{"action": "exit_now" | "new_exit_level" | "new_trail_pct",
 "params": {"price": 50000, "trail_pct": 0.02},
 "reason": "..."}
```

## Decision Logic — Winner 길게 / Default 보호
- AI exit > default ATR exit (멀면) → 채택 (winner 길게, aggressive)
- AI exit < default ATR exit (가까우면) → reject + use default (default 보호 floor)
- TRAIL: regime trend strong → trail_pct loosen (0.02 → 0.03)
- EXIT_NOW: regime crisis + position underwater → 즉시

## Allowed Tools
- Read (position, regime, market view)
- Emit exit order to executor

## Forbidden
- Cut winner short (NO, AI exit 더 가까우면 reject)
- Bypass default ATR floor (NO)
- Position size 변경 (NO, exit only)

## Failure Mode
- Timeout >5s → default ATR exit 사용
- AI 가 floor 위반 (default 보다 가까운 exit) → reject + log
- LLM rate limit → Python fallback (default ATR)

## SLA
- Latency: <5s
- Cost: ~$0.7/day P1

## Cross-ref
- [[ADR-004]] gate 7
- [[ADR-005]] ATR Stop/TP per-strategy (default floor)
- [[active-autonomous-vision]] §7 Adaptive Exit AI
- skill `gating-pipeline`
