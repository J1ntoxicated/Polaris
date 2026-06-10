---
type: ADR
adr_id: ADR-004
aliases: [ADR-004]
status: active
date_created: 2026-05-06
tags: [adr, ai, pipeline, langgraph, per-gate]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-005-sizing-formula-cell-routing|ADR-005]], [[ADR-006-cell-matrix|ADR-006]], [[active-autonomous-vision]]
reviewed_by: codex+jin (round 2 + Jin clarification 21:30)
---

# ADR-004 — Per-Gate AI Agent Pipeline

## Decision

Signal lifecycle 의 8 gate 각각에 AI agent supervisory. LangGraph-style state machine 으로 orchestrate. Cost ~$6/day (Haiku 2800 calls + Sonnet 200 calls).

## 8 Gate Pipeline

```
Layer 0 active universe (~270-320)
    ↓
[1] Universe Scanner — Haiku
    Input: active universe + cell_matrix score + ticker baseline
    Output: 30-ticker focus watchlist this cycle (cost optim)
    ↓
[2] Strategy Signal Generator — Python only (LLM 없음)
    7 strategies emit raw_signal (volume_burst / tsmom / rsi_bb / spot_donchian / fx_breakout / xau_indices / session_breakout)
    ↓
[3] Signal Validator — Haiku
    Input: raw_signal + cell_matrix routing + ticker baseline + recent same-ticker trades
    Output: PASS / KILL / MODIFY (strength scalar 0.5-1.5×)
    ↓
[4] Pre-Entry Watcher — Haiku, 30s loop (per-second decision)
    Input: validated_signal + tick stream
    Output: PROCEED / KILL (default 30s window)
    ↓
[5] Entry Sizer — Sonnet (P1) / Python deterministic (P0)
    Input: signal + portfolio_state + cell_matrix + ticker_baseline
    Output: notional + entry_type (market/ioc/limit) + slippage_tier
    Formula: T4 (1 scalar + tier amp + hard MAX) + cell routing mult
    ↓
[6] Position Monitor — Sonnet (P1) / Python (P0), N-sec loop
    Input: position + market_view + regime flag + recent ticks
    Output: HOLD / ADJUST_EXIT / EXIT_NOW / SWAP_STRATEGY
    ↓
[7] Adaptive Exit — Sonnet (P1) / Python (P0)
    Input: position + market regime + recent volatility
    Output: exit_now / new_exit_level / new_trail_pct
    Override default ATR×N exit when AI sees better point (winner 길게, default 보호)
    ↓
[8] Post-Trade Reflector — Sonnet (P1) / Python lesson template (P0)
    Input: closed_trade + market context
    Output: lesson + cell_matrix delta + learner adjustment
    Write to vault/50_research/lessons/
```

## Cost Estimate (~$6/day)

| Gate | Model | Calls/day | Tokens (avg) | Cost |
|---|---|---|---|---|
| Universe Scanner | Haiku | 288 (5min × 24h) | 2k in / 0.5k out | $0.4 |
| Signal Validator | Haiku | ~1500 (raw signals) | 1.5k in / 0.3k out | $1.5 |
| Pre-Entry Watcher | Haiku | ~1000 (30s × valid) | 1k in / 0.2k out | $0.8 |
| Entry Sizer | Sonnet (P1) | ~50 (entries) | 3k in / 0.5k out | $1.0 |
| Position Monitor | Sonnet (P1) | ~80 (active × N-sec) | 2k in / 0.3k out | $1.2 |
| Adaptive Exit | Sonnet (P1) | ~50 (exits) | 2k in / 0.4k out | $0.7 |
| Post-Trade Reflector | Sonnet (P1) | ~50 (closed) | 4k in / 1k out | $1.2 |
| **Total** | | | | **~$6.8/day** |

P0 = Sonnet 4 gate (entry/monitor/exit/reflect) Python deterministic 으로 대체 → cost ~$2.5/day.

## policy_engine 3-Layer (Round 3)

```python
# Layer 1: Matrix (mode × agent × action_class)
MATRIX = { (mode, agent, action): allowed_bool, ... }

# Layer 2: Per-action target validator
VALIDATORS = {
    "write_research": vault_path_predicate,
    "place_order": venue_symbol_predicate,
    "destructive_op": confirm_required_predicate,
}

# Layer 3: Event log
@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    event_id: str
```

## State Machine (LangGraph-style)

각 gate output 다음 gate input. Failure isolation:
- Gate exception → strategy_id HALT (Layer 7 circuit breaker)
- AI timeout (>5s Haiku, >15s Sonnet) → fallback Python deterministic
- AI rate limit → queue + retry
- AI provider down → 전체 system fallback Python (P0 path 동일)

## Failure Modes
- LLM hallucination → output schema strict pydantic validate, reject + log
- LLM cost spike → daily budget cap $10, 도달 시 Haiku-only mode
- Latency budget: gate 1-4 (Haiku) <2s end-to-end, gate 5-8 (Sonnet) <5s

## Phase
- P0: 4 Haiku gate live (1/3/4 + Python entry-sizer 5) + Python deterministic 6/7/8
- P1: Sonnet 4 gate upgrade (5/6/7/8) + Adaptive Exit override active
- P2: cross-gate optimization (e.g., Position Monitor → Adaptive Exit fusion)

## Sources
- Round 2 T2 + Jin clarification 21:30
- R4 리서치: LangGraph / TradingAgents (analyst→risk→executor 패턴)
- T11 archive: per-gate signal funnel SCOPE4
