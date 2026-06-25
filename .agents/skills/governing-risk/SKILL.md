---
name: governing-risk
description: Use to enforce hard caps (per-symbol, per-strategy, per-track, total daily), symbol-cluster caps (BTC/ETH 40%, XAU+indices 50%, FX majors 60%), risk-budget fill-rate weak-signal cut (>=70% triggers cut), Cold Start CS-3 boundaries, and strategy-scoped circuit breakers. Deterministic Python, no LLM (Round 3 BLOCKER fix).
---

# governing-risk (P0 skill)

## When to use
- Pre-order placement (gate 5 → executor)
- Strategy circuit breaker trigger
- Daily fill-rate threshold cross
- Manual halt (Jin / risk-officer)

## Inputs
- order_intent (sized)
- portfolio_state
- recent_strategy_event_log (rejects, NaN sizing, stale data)

## Enforcement (deterministic — risk-officer)

### Hard caps ([[ADR-005]])
- Reject if any cap violated
- Priority: hard MAX > Kelly (Kelly 산출치가 cap 초과 → 절단)

### Symbol-cluster cap (pre-strategy cap)
- BTC/ETH (spot): cumulative 40% max
- XAU+indices (CFD): cumulative 50% max
- FX majors (CFD): cumulative 60% max

### Fill-rate cut
- venue daily risk fill-rate ≥ 70% → weakest signal_strength 즉시 컷
- 손익 무관

### Cold Start CS-3
- per-strategy `n_closed_trades < 20` → Kelly off, single 6%/7%
- `n >= 20` → Kelly on, 8%/9%

### Strategy circuit breaker (Layer 7 mechanism 4)
- Trigger: 예외 / order reject storm (>5 in 1min) / NaN sizing / stale market data (>30s gap)
- Action: `strategy_id HALT` (1h auto-unblock per [[ADR-007]] adaptive_learner_attack)
- Continue: 나머지 6 strategy continue (granular kill-switch, mechanism 7)

### Destructive op confirm
- drop tables / rm -rf data / etc → explicit human confirm + ADR mint 의무

## Outputs
- PolicyDecision {allowed, reason, event_id, actions[]}
- Event log: `policy_decision`, `strategy_halt`, `strategy_resume`, `lever_change`

## NO LLM
- 모든 결정 = deterministic Python (Round 3 BLOCKER fix — LLM hallucination = capital risk)

## Failure handling
- Inconsistent state → emergency_halt + forensicist trigger
- Cluster cap calc error → reject all new orders + alert

## Cross-ref
- [[ADR-005]] hard caps + cluster cap + fill-rate
- [[ADR-003]] Layer 7 isolation primitives
- agent: risk-officer (deterministic Python, LLM 없음)
