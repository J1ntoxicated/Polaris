# AI Architecture Redesign — Spec

- **Date**: 2026-05-01
- **Author**: Claude (Opus 4.7) under Jin mandate
- **Sequential thinking**: 9 thoughts (Th1-Th9)
- **Vault grounding**: `[[INSIGHT-031]]`, `[[CLAUDE]]` 영속 원칙
- **Status**: Draft (pending Jin approval)
- **Trigger**: 미장 시작 (~23:30 AEST 2026-05-01) 전 최대 progress 필요

---

## 1. 동기 (Why)

### 데이터 (24h, 2026-05-01)

| Metric | Value |
|---|---|
| AI calls | 1,944 |
| AI cost | $18.83 |
| Stages | 7 (exit/entry/signal/strategy/portfolio/regime/tournament) |
| **AI advised trades WR** | **44.7%** (n=903) |
| **No-AI trades WR** | **55.1%** (n=1,947) |
| **WR delta** | **-10.4pp (AI worse)** |
| **avg pnl AI vs no-AI** | **-$3.11 vs -$0.26 (12x worse)** |

### Jin mandate

> "AI 코스트가 뛰었어. 이거 맞아? 우리 AI 구조 전체 리뷰."
> "쓰는건 상관 없어 적절한거라면."
> "근본적으로 다 다시 생각해봐 뭐가 제일 수익이 많이 날지."
> "방향 잘 잡아서, 미장 시작 전까지."

### 진단 — Advisor 패턴의 7 가지 결함

1. **Latency mismatch**: AI 3s vs scalp 1s → stale
2. **Decision authority 불명확**: rule fallback vs AI → 양쪽 다 손해
3. **Outcome decoupling**: AI advice → trade 결과 link 없음
4. **Cost-scaling 잘못**: position 늘면 비용 폭발
5. **Context 제한**: 단편 정보만, 시스템 전체 안 봄
6. **Wisdom inverted**: 1944번 stateless 호출 << batch pattern
7. **측정 부재**: 1년+ 운영, 가치 입증 INSIGHT 0

→ **Advisor 는 AI 단점만 활용 (latency/stateless/cost), 장점 (pattern/creativity) 무시**

---

## 2. 새 Architecture — 4-Layer

```
Layer 0  데이터 ingestion (Tech / Crowd / News / Funding / Microstructure / Regime)
   │
   ▼
Layer 1  SIGNAL FUSION  (rule-based, real-time)
        conviction_score = Σ wi × signal_i
   │
   ▼
Layer 2  봇별 EXECUTION  (각 봇 5-10 strategy)
   │
   ▼
Layer 3  CELL MATRIX learning  (5-dim per bot)
   │
   ▼
Layer 4  AI META-COACH  (4 modes, batch + event)
        M1 Daily Pattern   (1×/일)
        M2 Weekly Curator  (1×/주)
        M3 Hourly Audit    (24×/일)
        M4 Event Crisis    (event-triggered)
```

### 핵심 원칙

1. **Layer 1-3 = real-time decision** (rule + cell matrix). AI 호출 0.
2. **Layer 4 = AI** = batch/event coach. **Advisor 패턴 폐기**.
3. **모든 AI 호출 → outcome trace 의무**.
4. **봇별 alpha source 명확화** + strategy 다이어트.

---

## 3. 봇별 alpha source 가설

| 봇 | Primary alpha | 핵심 데이터 | Target |
|---|---|---|---|
| **SPOT crypto** (24/7 scalp) | microstructure + funding decoupling | books5, taker flow, SPOT-SWAP basis | WR 65% R:R 1:2 |
| **SPOT stock** (RTH trend) | news + sector + technical breakout | news API, FINRA, Donchian, MACD | WR 55% R:R 1:3 |
| **CFD Capital** (forex/idx/com) | macro regime + breakout + carry | macro events, BB break, time-zone | WR 45% R:R 1:5 |
| **CFD OKX SWAP** (crypto perp) | funding + L/S contrarian | funding rate, L/S ratio | WR 55% R:R 1:2 |

### Strategy 다이어트
- 현재: 100+ strategies (분산, sparse, 가치 측정 어려움)
- 신규: **봇당 5-10 = 총 ~25**
- 효과: cell sparse 빠른 학습, 명확한 alpha 정의

---

## 4. AI 4-mode (Layer 4 detail)

### M1: **Daily Pattern Synthesis** (1×/일, ~$0.5-1/일)
- **입력**: 24h trades (groupby strategy/regime/ticker), market state, signal stats
- **출력 (JSON)**:
  ```json
  {
    "patterns_found": [...],
    "weight_adjustments": {"signal_x": 0.8, "signal_y": 1.2},
    "cell_tuning_recommended": {...},
    "warnings": [...]
  }
  ```
- **Bot 행동**: 봇이 자동 적용 (cell_matrix update)
- **측정**: 적용 전후 24h KPI delta

### M2: **Weekly Strategy Curator** (1×/주, ~$0.5-2/주)
- **입력**: 모든 strategy elo / outcome / mutation history
- **출력**: 신규 strategy 후보 prompt + 죽일 strategy 명단
- **Bot 행동**: tournament 에 새 후보 추가, 폐기 strategy 비활성
- **측정**: 새 strategy 1주 elo

### M3: **Hourly Northstar Audit** (24×/일, ~$0.5/일)
- **입력**: 시스템 KPI snapshot (NET, WR, exit_dist, regime, cell_sparse, drawdown)
- **출력**: 북극성 위반 감지 + 자동 수정 제안
- **Bot 행동**: violation 시 ParamRegistry 자동 수정 (또는 alert)
- **측정**: 위반 → 수정 → 회복 시간

### M4: **Event Crisis Coach** (event-triggered, ~$0.1-1/일)
- **Trigger**: drawdown >5%, regime crisis_high, 1h NET <-$500, anomaly
- **입력**: 위기 컨텍스트 + 시장 state + 최근 trades
- **출력**: 권장 행동 (size cut % / exchange shutoff / strategy pause)
- **Bot 행동**: 자동 적용 또는 high-priority alert
- **측정**: crisis 후 회복 시간

### 비용 예상

| Mode | Calls/일 | Cost/일 |
|---|---|---|
| M1 Daily | 1 | $1.0 |
| M2 Weekly | 0.15 | $0.3 |
| M3 Hourly | 24 | $0.5 |
| M4 Event | 0-3 | $0.5 |
| **Total** | **~30** | **~$2.3** |

대비 현재 $18.83/일 → **88% ↓** + 모두 측정 가능.

---

## 5. Outcome Trace Schema (단계 2)

### ai_calls 컬럼 추가

```sql
ALTER TABLE ai_calls ADD COLUMN decision_taken INTEGER DEFAULT 0;
ALTER TABLE ai_calls ADD COLUMN action TEXT;
ALTER TABLE ai_calls ADD COLUMN outcome_window_sec INTEGER;
ALTER TABLE ai_calls ADD COLUMN outcome_pnl_change REAL;
ALTER TABLE ai_calls ADD COLUMN outcome_kpi_change TEXT;  -- JSON
ALTER TABLE ai_calls ADD COLUMN mode TEXT;  -- M1/M2/M3/M4 (신규)
```

### 가치 측정 SQL

```sql
SELECT mode,
  COUNT(*) calls,
  ROUND(SUM(cost),2) cost,
  ROUND(AVG(outcome_pnl_change),3) avg_pnl_delta,
  ROUND(SUM(outcome_pnl_change) / NULLIF(SUM(cost),0), 2) roi
FROM ai_calls
WHERE ts >= now-7d
GROUP BY mode
ORDER BY roi DESC;
```

ROI < 0 mode → 즉시 폐기.
ROI > 5x → 강화.

---

## 6. 구현 단계 (우선순위)

### 🔴 단계 1 (오늘 즉시, 1-2h) — 노이즈 제거

목적: 미장 시작 전 AI cost 92% ↓ + advisor 패턴 정지.

**Tasks**:
1. `live_config.json` `ai_active_modes: ["strategy_evolution"]` flag 도입
2. `ai/orchestrator.py` 에 `is_active(stage)` 체크 추가
3. exit_advise / entry_judge / signal_augment / portfolio_intel / regime_advice / tournament_ai → skip
4. strategy_evolution (M2 prototype) 만 유지
5. 봇 restart

**예상 효과**: $18 → $1-2/일 (M2 evolution 만)

### 🟡 단계 2 (1-3일) — Outcome trace

목적: 모든 AI 호출의 ROI 측정 가능.

**Tasks**:
1. ai_calls schema extend
2. AI orchestrator 에 outcome trace wrapper
3. M1 Daily 첫 prototype (간단 pattern detect)
4. M3 Hourly Audit 첫 prototype (북극성 KPI check)
5. 1주 데이터 수집

### 🟢 단계 3 (3-7일) — Signal Fusion

목적: 데이터 source 다 활용해서 conviction score.

**Tasks**:
1. `invasion/signal/fusion.py` — 모든 signal 통합 score 계산
2. 봇별 weights config + cell_matrix dim 추가
3. 1 봇 (SPOT crypto) 부터 적용
4. Dry-run + outcome 비교

### 🔵 단계 4 (1-2주) — Strategy diet

목적: 봇당 5-10 strategy. cell sparse 빠른 채움.

**Tasks**:
1. 봇별 alpha source ADR
2. 살아남는 strategies 선정 (elo + alpha source 정합)
3. 폐기 strategies → tournament 비활성
4. 1-2주 새 cell_matrix 학습 검증

### ⚪ 단계 5 (2-4주) — AI 4-mode 완성

목적: M1/M2/M3/M4 모두 구현 + advisor 패턴 완전 폐기.

**Tasks**:
1. M1 Daily 본격 (pattern + cell tuning)
2. M2 Weekly (strategy curator + 후보 진화)
3. M3 Hourly (autonomous param fix)
4. M4 Event (crisis coach + auto size cut)
5. Old AI 코드 archive

---

## 7. 미장 시작 (~23:30 AEST 2026-05-01) 까지 deliverable

**필수**:
- [x] Spec doc (이 문서)
- [ ] ADR-006 — AI advisor deprecation 결정 기록
- [ ] 단계 1 적용 — `ai_active_modes` flag + 봇 restart
- [x] INSIGHT-031 (vault 기록 완료)

**선택 (시간 허락 시)**:
- [ ] 단계 2 prototype — outcome trace schema + 1 wrapper

미장 시작 시 봇은 **단계 1 상태**: advisor 5개 disable, strategy_evolution 만 (M2 prototype).
나머지 단계 (2-5) 는 며칠 / 몇 주 점진 적용.

---

## 8. 정합성 (북극성 self-review)

| Mandate | 정합 |
|---|---|
| `feedback_aggressive_always_profit` | ✅ Layer 1 rule 적극, AI noise 제거 |
| `feedback_loss_profit_asymmetry` | ✅ M1/M4 가 비대칭 패턴 발견 |
| `feedback_no_quick_patch_ever` | ✅ 5단계 점진, patch X |
| `feedback_no_block_filter_architecture` | ✅ 차단 X, AI 위치 재정의 |
| `feedback_overhaul_over_incremental` | ✅ advisor → meta-coach 전면 재정의 |
| `feedback_root_cause_evidence_based` | ✅ 데이터 (WR -10pp) 기반 |
| `feedback_adaptive_learner_attack` | ✅ M1/M2 가 진화 loop |
| `feedback_northstar_full_authority` | ✅ M3 가 자동 audit + 수정 |
| `feedback_sequential_superpowers_vault_organic` | ✅ 9-thought sequential + vault |

---

## 9. Risk + counter (debate)

### Pro
- 데이터 명확 (AI 가 outcome 악화)
- Cost 92% ↓
- 8 mandate 정합
- Reversible (flag 토글)

### Anti
- Selection bias 가능 (AI 가 risky trade 에 호출됨)
- 1년+ prompt 자산 폐기
- Long-term evolution 일시 정지
- 봇이 AI 없이 검증 안됨

### 절충
- 단계 1 즉시, **strategy_evolution 만 유지**
- 2주 데이터 후 단계 2 outcome trace 결정
- 점진 + 측정 = 위험 최소

---

## 10. Approval

- [ ] Jin reviews this spec
- [ ] Approve → 단계 1 즉시 적용 + ADR-006 작성
- [ ] 단계 2-5 점진 적용 (writing-plans skill)
