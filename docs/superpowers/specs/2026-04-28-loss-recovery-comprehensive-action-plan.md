# Loss Recovery — Comprehensive Action Plan

**Date**: 2026-04-28 20:30
**Status**: Draft (Jin review)
**Skill**: superpowers:brainstorming → writing-plans (next)
**Vault refs**: [[INSIGHT-016]] [[INSIGHT-017]] [[INSIGHT-021]] [[INSIGHT-024]]
[[INSIGHT-025]] [[INSIGHT-026]] [[INSIGHT-027]] [[INSIGHT-028]] [[ADR-003]] [[ADR-005]]

## Context

**24h NET -$2340 / WR 50.2%**. Bot restart 후 1h NET -$164 sustained. Cell-aware
override 22.6% (학습 sparse) 만 fire. AI HOLD "sparse" 1200건/24h.

**4-symptom analysis (vault grounded)**:

| # | Symptom | 24h drag | Root |
|---|---|---|---|
| A | broker_sync adopt empty strategy_id (88 alpaca stock) | -$514 | code bug ([[INSIGHT-028]]) |
| B | session_breakout_london chronic loser (commodity short) | -$222 | architectural ([[INSIGHT-024]]) |
| C | crypto_specialist_g297_bayes persistent loser (8 tickers) | -$140 | architectural (Tournament `ELIMINATED_LOG` only — [[INSIGHT-025]]) |
| D | natural selection chain 부재 | $1500+ silent | [[INSIGHT-025]] block paradigm 폐기 50% only |

**D 가 B/C 의 root** — DEMOTE 폐기 + Tournament Elo floor 폐기 + evolver pruning
폐기 + cell.mult floor 1.0 → 자연 도태 0 mechanism, chronic loser 무한 base size.

## Decision Matrix

### Tier 0 (Immediate code fix)

**A. broker_sync empty strategy_id fix** (`broker_sync.py`)
- adopt 시 `adopted_pending` sentinel 가 trade 마감까지 유지되도록 보장
- 또는 fallback `adopted_alpaca_stock_unknown` 채움 (cell_matrix entry 가능)
- ROI: $514/24h drag 직접 차단

### Tier 1 (Architectural — natural selection)

**B-D. cell.mult dynamic ramp-down** (chronic loser size throttle)
- 현재: cell.mult lower bound 1.0 (ADR-003 amplify-only)
- 새: cell.score 기반 dynamic
  - score > 0.5: mult > 1.0 (winner amplify, 기존)
  - 0.0 ≤ score ≤ 0.5: mult = 1.0 (base, 기존)
  - -0.10 ≤ score < 0.0: mult = 0.7 (early warning ramp-down)
  - score < -0.10: mult = 0.5 (chronic, half size)
- amplify-only 정합 (block 아닌 capacity throttle. INSIGHT-025 정의: status='disabled'
  만 block, size dampen 은 capacity)
- ROI: B+C drag $362+ + 미발견 chronic 누적 $500+/day

### Tier 2 (Tune — INSIGHT-016 idea pool 후속)

**E1. Direction-aware TIME** (#3) — OKX short asym
- short side WR 더 낮은 strategy → short max_hold 짧게
- ROI: ~$200/day est.

**E2. Asset-group 별 TIME** (#7) — CAP commodity catastrophe
- crypto/forex/commodity 별 max_hold 차별
- commodity 짧게 (catastrophic loss 깊이 제한)
- ROI: ~$300/day est.

**E3. Entry score-based hold** (#4)
- signal score 강함 → longer hold / 약함 → short
- 학습 채워진 후 자연 cap

### Tier 3 (Long-term — Phase 3)

**F. AI Controller HOLD threshold tune**
- 현재 cell-aware fallback "sparse" → default HOLD 5min override
- Sparse threshold 강화 (sample n>=5 만 fallback, n<5 면 base preg)

**G. Strategy spawn weighting**
- mutation rate 가 winner cell 위주 spawn
- 현재 INSIGHT-025 Phase 2 적용 (mutation rate decay)

## Recommended Approach

**Phase 1 (즉시, 1-2h within)**:
- Tier 0 A (broker_sync fix) — code change, dev-coder dispatch
- Tier 1 B-D (cell.mult dynamic) — architectural, separate spec + plan

**Phase 2 (1-3 days)**:
- Tier 2 E1/E2 (direction/group-aware TIME) — preg + reader
- Tier 3 F (AI HOLD threshold) — observation 후 tune

**Phase 3 (1+ week)**:
- Tier 3 G (mutation weighting) — INSIGHT-025 Phase 2 effect 측정 후

## North Star Alignment

- ✅ Block paradigm 0 (Tier 1 cell.mult dynamic = capacity throttle, not block)
- ✅ Amplify-only mandate spirit — winner protect + chronic loser detection (block 아닌 size 비례)
- ✅ `feedback_loss_profit_asymmetry` 정합 (loss 깊이 제한)
- ✅ `feedback_overhaul_over_incremental` (Tier 1 architectural, not patch)
- ⚠️ ADR-003 amplify-only clamp (lower bound 1.0) 와 cell.mult ramp-down 명확 정합 — block paradigm
  정의 (INSIGHT-025): `status='disabled'` 만 block. size dampen 은 capacity throttle.

## Risks

### Tier 0
- broker_sync fallback strategy 가 cell_matrix 에 잘못 분류 — `adopted_alpaca_stock_unknown` cell 이 모든 RTH adopt 받음 → 그 cell 의 학습값이 다양한 ticker 의 평균. 수용 가능 (개별 ticker 보다 나음).

### Tier 1
- amplify-only mandate 위반 인식 가능. INSIGHT-025 의 block paradigm 정의 (`status='disabled'`)
  와 size dampen 분리 정합화 필요.
- chronic loser 의 winner cycle 차단 risk — score < -0.10 면 winner 가능성 매우 낮음, 차단 정당.

### Tier 2
- INSIGHT-016 idea pool 의 nuance — direction-aware TIME 가 winner short 도 단축 가능.
  cell-aware learner 가 자연 cap 하지만 학습 sparse 시 fallback 짧음.

## Verification Plan

**Phase 1 deploy 후**:
- Tier 0 A: NULL strategy trades 0/24h (vs 88) — broker_sync fix 효과
- Tier 1 B-D:
  - chronic strategy (cell.score < -0.10) 의 trade size 50% 감소 측정
  - 24h NET swing measure (-$2340 → ?)

**Phase 2-3 deploy 후**:
- Direction/group TIME 효과 — TIME drag $2948 → ?
- AI HOLD frequency — 1200건/24h → ?
- Mutation rate decay — strategy population 변화 (chronic 자연 retire)

## Action Plan Summary

```
Phase 1 (immediate, 1-2h):
  ✅ Tier 0 A — INSIGHT-028 broker_sync fix
  ✅ Tier 1 B-D — INSIGHT-025 cell.mult dynamic ramp-down spec

Phase 2 (1-3 days):
  ⏳ Tier 2 E1 — Direction-aware TIME (INSIGHT-016 #3)
  ⏳ Tier 2 E2 — Asset-group TIME (INSIGHT-016 #7)
  ⏳ Tier 3 F — AI HOLD sparse threshold tune

Phase 3 (1+ week):
  ⏳ Tier 3 G — Mutation rate weighting (INSIGHT-025 Phase 2 후속)
```

## Open INSIGHTs Status

| INSIGHT | Resolution Tier |
|---|---|
| 016 TIME exit dominates | Tier 2 E1+E2 (idea pool 후속) |
| 017 CAP commodity short sustained | Tier 1 B-D (architectural) |
| 018 sentiment myfxbook write gap | 별도 (data layer) |
| 021 OKX crypto 1-2h death zone | Tier 2 E1 |
| 024 CAP commodity fitness deficit | Tier 1 B-D |
| 025 evolver/tournament block paradigm | Tier 1 B-D + Tier 3 G |
| 026 STOP exit size asymmetry | 별도 (sizing layer) |
| 027 cell_matrix UPSERT | ✅ applied |
| 028 broker_sync empty strategy | Tier 0 A |

## Decision

**Phase 1 즉시 진행**:
- Tier 0 A spec → dev-coder forensic + fix
- Tier 1 B-D spec → 별도 brainstorm + plan (architectural)

**Phase 2 spec 작성** (Phase 1 효과 측정 후):
- Tier 2 E1+E2 — INSIGHT-016 idea pool 후속

Jin review 후 Phase 1 dispatch.
