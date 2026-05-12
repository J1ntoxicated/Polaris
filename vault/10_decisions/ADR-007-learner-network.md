---
type: ADR
adr_id: ADR-007
status: active
date_created: 2026-05-06
tags: [adr, learner, t11, auto-tune]
related: [[ADR-003]], [[ADR-004]], [[ADR-006]], [[aggressive-bias]]
reviewed_by: codex+jin (T11 archive carryover + adaptive_learner_attack 영속 원칙)
---

# ADR-007 — Learner Network (7 learner, hourly auto-tune)

## Decision

7 learner hourly auto-tune. T11 archive 6 learner + 1 AI feedback (post-trade reflector → strategy weight).

## 7 Learners

| # | Learner | Tunes | Trigger | P0 priority |
|---|---|---|---|---|
| 1 | session_mult | session × strategy WR multiplier | 매 trade close | **P0** |
| 2 | regime_mult | regime × strategy size multiplier (WR≥55% +0.1, ≤40% -0.1) | regime flip + trade close | **P0** |
| 3 | max_hold | bucket avg_pnl 기반 max holding bars | trade close | **P0** |
| 4 | profit_target | winner p75 peak × 1.5 | winner close | P1 |
| 5 | trail_mult | TRAIL retention ratio | trail close | P1 |
| 6 | bep_activate | BEP avg_peak threshold | BEP-touched close | P1 |
| 7 | ai_feedback | post-trade reflector → strategy weight | LLM lesson emit | P1 |

## P0 = 3 priority (session_mult / regime_mult / max_hold)
P1 = 4 추가 (profit_target / trail_mult / bep_activate / ai_feedback)

## adaptive_learner_attack 원칙 (영속)

1. **관대 default**: false positive 감수, 공격량 유지. 보수 도파민 X
2. **일시 차단** (auto-unblock): 1h 후 자동 해제. 영구 block X
3. **Specific (triple)**: ticker × strategy × regime triple 단위 차단. aggregate (전체 strategy) 억제 X
4. **Toggle**: learner 실패 시 hardcoded default 복귀. learner 폭주 방지

## Schema

```sql
CREATE TABLE learner_state (
  learner_id TEXT,    -- session_mult / regime_mult / ...
  key TEXT,           -- e.g., "asia:volume_burst" (composite)
  value REAL,
  n_samples INTEGER,
  updated_at INTEGER,
  PRIMARY KEY (learner_id, key)
);
```

## Hourly Tune Job

```python
async def learner_tune_loop():
    while True:
        for learner in active_learners():
            try:
                deltas = learner.compute_deltas(recent_trades=last_hour())
                for key, delta in deltas.items():
                    apply_with_clamp(learner.id, key, delta)
            except Exception as e:
                log_event("learner_failure", learner_id=learner.id, error=e)
                # toggle off → default fallback (영속 원칙 4)
        await sleep(3600)
```

## Conflict Resolution

여러 learner 가 같은 cell 에 영향:
- session_mult × regime_mult × cell_routing_mult = multiplicative chain (independent)
- 각 multiplier clip [0.3, 3.0] (sanity bound)
- 최종 product clip [0.1, 5.0] (안전망)

## Rollback

- 매 hour learner_state snapshot → `data/learner_snapshots/<ts>.parquet`
- 24h rolling expectancy 갑자기 -50% 이상 악화 → 직전 snapshot 복원 (manual trigger via skill `tuning-learners`)
- Permanent rollback X (영속 원칙 2: 일시만)

## Triple Specific Block

차단 발동 조건 (학습 기반, learner #2 regime_mult 결합):
- (ticker, strategy, regime) triple 의 최근 20 trades WR ≤30%
- → 해당 triple size_mult 0.3 으로 1h
- 1h 후 auto-unblock → 다시 평가
- 영구 block X, aggregate (strategy 전체) 억제 X

## Cell Matrix Interaction

- learner = matrix 의 cell 별 multiplier 조정
- `final_size_mult = session_mult × regime_mult × cell_routing_mult × ai_feedback_weight`
- Cell matrix score = trade outcome → learner update → 다음 cycle multiplier

## AI Feedback (#7) Flow

```
[Post-Trade Reflector — Sonnet] → lesson emit (vault/50_research/lessons/)
    ↓
[ai_feedback learner] → strategy weight delta (Δw ∈ [-0.1, +0.1])
    ↓
strategy weight × cell routing → next cycle sizing
```

## Phase
- P0: 3 priority (session/regime/max_hold) + hourly job + rollback snapshot
- P1: 7 full + AI feedback + triple specific block live
- P2: meta-learner (learner of learners, hyperparam tune)

## Sources
- T11 archive: `feedback_adaptive_learner_attack` 영속 원칙
- Memory: `feedback_adaptive_learner_attack.md`, `feedback_loss_profit_asymmetry.md`
- Jin clarification 21:30 (learner network 핵심)
