---
name: risk-officer
type: agent
status: active
date_created: 2026-05-06
tags: [agent, dev-ops, python, deterministic, no-llm]
related: [[ADR-005]], [[ADR-003]]
model: none  # Python policy_engine deterministic, LLM 없음 (Round 3 BLOCKER fix)
---

# risk-officer (Dev/Ops, Python deterministic — LLM 없음)

> **Sub-agent 헤더 (의무)**: DEMO/PAPER 전용(가상 자금) · aggressive bias 보존 · 거부 키워드 sweep 0건 (SSOT: CLAUDE.md rejection-keywords 블록) · vault r·w (brain contribution) — [[harness-collab-protocol]]

## Role
**Round 3 BLOCKER fix**: 모든 risk decision = deterministic Python policy_engine. LLM 결정 금지 (hallucination 위험 = capital risk).

`policy_engine.py` 3-layer 의 enforcement entity. Strategy halt/resume 결정. Hard cap fence. Destructive op confirm gate.

## Responsibilities
- Hard cap enforcement (per-symbol, per-strategy, per-track, total)
- Symbol-cluster cap enforcement (BTC/ETH 40%, XAU+indices 50%, FX majors 60%)
- Risk-budget fill-rate cut (≥70% → weakest signal)
- Strategy circuit breaker trigger (Layer 7 mechanism 4)
- Strategy halt/resume command emit
- Destructive op (drop tables / rm -rf data) confirm gate
- emergency_halt cross-mode privilege

## Input (deterministic)
- Order intent (signal + sized notional)
- Portfolio state
- Daily risk fill-rate
- Recent strategy event log (circuit breaker triggers)

## Output
```python
@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    event_id: str
    actions: list[str]  # e.g., ["halt:strategy=volume_burst", "cut:weakest_signal"]
```

## Allowed Tools
- Read (portfolio, event log)
- Write (event log only via state_store)
- Bash (process kill for strategy halt)

## Forbidden
- LLM call (NO — Round 3 BLOCKER fix, LLM hallucination = capital risk)
- Cell matrix mutation (NO)
- Order placement direct (NO, executor 책임)
- Strategy code edit (NO)

## Algorithm (deterministic)
```python
def check(mode, agent, action, target, ctx) -> PolicyDecision:
    # Layer 1: matrix lookup
    if not MATRIX.get((mode, agent, action), False):
        return reject("matrix_deny")
    # Layer 2: validator
    validator = VALIDATORS.get(action)
    if validator and not validator(target, ctx):
        return reject("validator_deny")
    # Layer 3: event log
    return allow(event_id=emit_event(...))
```

## Cross-ref
- [[ADR-005]] hard caps + cluster cap + fill-rate cut
- [[ADR-003]] Layer 7 isolation primitives (circuit breaker)
- Round 3 D2 BLOCKER fix (LLM → Python)
