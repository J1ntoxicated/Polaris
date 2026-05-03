---
entity_type: insight
entity_id: INSIGHT-002
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles]]", "[[ADR-002]]", "[[60_alpha/_README]]"]
mode: alpha
reviewed_by: codex
maturity: authoritative
authoritative_basis: Codex 디베이트 3 라운드 합의 (P5 정의)
tags: [type/insight, status/active, scope/alpha, priority/p1, polaris]
---

# INSIGHT-002 — MTTR-alpha 정의 + 측정 방법

> Polaris의 주 KPI. P5 (Alpha-first KPI) 핵심. Codex 권장.

## Context

P5 (Alpha-first KPI) 채택 시 "거래 성과 회복 시간"을 어떻게 정량화할지 codex 디베이트 2 라운드에서 합의.

## Definition

**MTTR-alpha = Mean Time To Recovery of Alpha**

성과 이상 탐지(out of control band) 시점부터 control band 내 N trade 또는 X일 연속 복귀까지 시간.

### Control Band 정의 (per-strategy / per-cell)

| 메트릭 | Control Band 기본값 | 비고 |
|---|---|---|
| Rolling Sharpe (30 trades) | μ ± 2σ (per-cell baseline) | baseline은 BACKTEST 기간 또는 PAPER pass 시점 |
| Hit Rate (30 trades) | μ ± 2σ | |
| MDD (rolling 30 trades) | < 1.5 × baseline MDD | |
| Expectancy (per trade) | > baseline expectancy × 0.5 | |

**이상 탐지**: 4 메트릭 중 1개라도 control band 벗어나면 "out of control".

**복귀**: 4 메트릭 모두 control band 내 + N trade (기본 N=20) 연속 또는 X일 (기본 X=3) 연속.

### MTTR-alpha 계산

```
MTTR-alpha = (복귀 시점) - (이상 탐지 시점)
```

단위: 시간 (hours).

## Evidence (모태 데이터)

모태 spot operation 기간 (2026-04-11 ~ 05-03, 22일):
- 수많은 손실 사이클 — 이상 탐지부터 fix까지 평균 며칠 (정확 측정 X)
- INSIGHT-032 (OKX SPOT scalp 수학적 불가능)는 12h 운영 후 발견 — 이는 fix가 아니라 fundamental impossibility
- Polaris MTTR-alpha 목표: < 24h (이상 탐지 → fix → control band 복귀)

## Auxiliary Metrics

P5에서 정의한 보조 KPI:
- **drawdown half-life**: 회복 절반 도달 시간
- **recovery area**: 회복 전까지 누적 기대수익 손실 면적 (integral)
- **diagnosis-to-patch**: 이상 발견 → fix 코드 작성
- **patch-to-stable**: fix → control band 안정

## Implementation

### Phase 1 (인수)
- 모태 `data/edge_calibration.json` (cell별 Bayesian Beta) → Polaris control band baseline 후보

### Phase 2 (첫 컴포넌트)
- `60_alpha/active/HYPOTHESIS-001` 통과 시 그 strategy의 control band baseline 정착

### Phase 4 (점진 확장)
- MTTR-alpha 측정 시작 (cron 또는 dataview)
- `vault/00_now/_NOW.md`에 현재 MTTR-alpha 표시 (dataview)
- 월별 trend 추적 (`vault/50_runtime/mttr_alpha_monthly.md`)

## Vault 품질 = Derived Metric

Codex 핵심 통찰:
> "vault orphan 0"은 KPI 아님. KPI는 거래 성과. vault 품질은 MTTR-alpha 단축 효과로만 측정.

→ vault 작업이 MTTR-alpha 단축에 기여 못 하면 그 vault 작업은 메타 작업 폭증 신호 (INSIGHT 작성 후 운영 모델 재검토).

## Recommendation

- [ ] Phase 1: 모태 edge_calibration → control band baseline 추출 후보
- [ ] Phase 2: 첫 strategy의 control band 정착 (HYPOTHESIS-001 BACKTEST 결과 기반)
- [ ] Phase 4: MTTR-alpha 자동 측정 + dataview 표시
- [ ] 월간 derived metric 리뷰 (vault 품질 = MTTR-alpha 단축 효과)

## Related

- principles P5 (Alpha-first KPI)
- ADR-002 (Vault-first architecture — vault 품질 derived 명시)
- 60_alpha/_README (워크플로)
- _INHERIT_QUEUE (edge_calibration 인수)
