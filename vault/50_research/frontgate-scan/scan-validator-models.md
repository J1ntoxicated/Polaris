---
type: research
status: active
date_created: 2026-07-11
tags: [frontgate, scan, meta-label, calibration, regime, timing]
---

# Scan — G3/G4/G5 강화 (메타라벨·캘리브레이션·레짐·타이밍·비용)

우리 관점 정리. 전 후보 = 스코어/사이징-값 입력. 사이징 접점은 전부 **기존 T4
continuous scalar(0.75–1.5)의 값 설정**으로 한정 — 신규 multiplier 0, 9-stack 불변.

## 후보
| 후보 | 증거 | 데이터 | 우리 처분 |
|---|---|---|---|
| Triple-barrier 메타라벨 (López de Prado AFML 2018) | A | 자가 축적 — `meta_labels` 수집 중 | **P10** — 라벨 카운트 게이트 |
| 확률 캘리브레이션 (Platt 1999 / isotonic PAV) | A | 자가 축적(gate_event_log/shadow_log) | **P4** — Platt→isotonic 단계 승격 |
| HMM 레짐 (Hamilton 1989 계보, hmmlearn) | B | 있음(수익률·변동성) | 저순위 — 기존 레짐 스택 대체 엔진 섀도우만 |
| VWAP/AVWAP 타이밍 (QC Lean 참조) | A(실무 표준) | 있음(1m) | **P6** — G4 타이밍 지연 성분 |
| Almgren-Chriss 비용모델 | B | L2 오더북 없음 | 보류 — `net_edge.py` 프록시 보강만 |

## R1 디베이트 반영 (핵심 2건)
- **메타라벨 T4 구동 게이트**: 라벨 수집은 지금 지속하되, 학습 모델의 스칼라 구동은
  비중첩 라벨 pooled ~2k+ · 전략군당 ≥500 · purge/embargo 적용 후. 미달 = 섀도우 병기만.
- **캘리브레이션 순서**: isotonic 성급 적용 = staircase 픽션. **Platt 선행**(결정-결과
  pair 500–1k) → isotonic 승격(1.5k–3k, 확률 bin 커버리지 확인). 롤링 OOS, fit/eval
  윈도 분리. 양방향 교정(과소신 상향 포함) — 감쇠 아님, mandate 안전.

## 구현 노트
- mlfinlab = 상용 라이선스(vendoring 불가) → 방법론만 자체 구현. 라벨부는 이미 자체
  구현 완료(`polaris/core/learners/meta_label.py`).
- PAV ~50 LOC 의존성 0 자체 구현, `learners/posterior.py` NIG와 교차검증.
- 메타라벨 출력 = `regime_fit.regime_scalar` 패턴으로 기존 scalar 값 설정(floor 0.75).
- HMM: `normalize_regime` 소스에 병렬 레짐 라벨 컬럼 섀도우 → agreement 후 교체 검토.

통합 설계 → [[integration-blueprint]] · 실험 계획 → [[experiment-roadmap]]
