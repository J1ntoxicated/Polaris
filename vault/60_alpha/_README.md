---
entity_type: index
entity_id: alpha_readme
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INDEX]]", "[[principles]]", "[[INSIGHT-002]]", "[[_alpha_index]]"]
mode: meta
reviewed_by: codex
tags: [meta, alpha, polaris]
---

# 60_alpha — 가설 검증 워크플로

## 목적

Polaris의 알파/리스크/시장 적합성 검증 1차 영역. P5 (Alpha-first KPI) 핵심.

## 디렉토리 구조

```
60_alpha/
├── _README.md         (이 파일)
├── _alpha_index.md    (dataview index — status별)
├── active/            (가설 진행 중)
├── graduated/         (ADR 승격됨 → 라이브)
└── archived/          (실패/폐기)
```

## 워크플로 (4 boundary + 1 fast-fail)

```
1. HYPOTHESIS 작성 (Jin 또는 codex 추천 → vault-curator stub)
   → vault/60_alpha/active/HYPOTHESIS-NNN-<name>.md
   → 템플릿: .templates/HYPOTHESIS

2. FAST-FAIL GATE (BACKTEST 24h 내)
   - 수학적 생존 가능성 (예: fee × 2 < expected_TP)
   - 기각 시 → INSIGHT 작성 후 archived/

3. BACKTEST
   - 정량 임계값 통과 (Sharpe / hit rate / MDD / expectancy)
   - 결과 노트 update (HYPOTHESIS-NNN 안)

4. PAPER (BACKTEST pass 시)
   - 최소 N trades or X일 연속
   - slippage gap 허용 범위

5. PROMOTION GATE (PAPER → ADR)
   - [ ] paper/live behavior diff audit
   - [ ] sizing cap 명시
   - [ ] kill criteria 명시
   - [ ] rollback plan 명시
   - 통과 시 → ADR-NNN 작성 후 graduated/

6. ADR 적용 (라이브) — Jin ack 후
   - 모니터링 시작 (control band)
   - 이상 발생 시 forensic 모드 진입 → INSIGHT 추가
```

## 실패 가설 처리

실패 시 ADR 승격 X — INSIGHT/lesson으로 닫음 (decision layer 오염 방지).

## State 관리

frontmatter `status`:
- `#status/active` — active/ 디렉토리, 진행 중
- `#status/graduated` — graduated/ 디렉토리, ADR 승격됨
- `#status/archived` — archived/ 디렉토리, 실패/폐기

`expires`: 결과 도달 +30일

## Codex 추천 첫 가설 (Phase 2 후보)

- **HYPO-001**: cboe_vix_term + neutral regime vs dual_thrust (Bayesian 비교)
  - 모태 edge_calibration.json: cboe_vix_term n=254, dual_thrust n=200
- **HYPO-002**: volatility_spike 전략 (ELO 4391) vs baseline
  - 모태 tournament_elo.json: volatility_spike top 1

## 메트릭 (P5)

각 가설 결과는 [[INSIGHT-002]] 정의된 control band에 기여:
- BACKTEST → control band baseline 후보
- PAPER → control band 검증
- 라이브 운영 → MTTR-alpha 측정 시작
