---
type: research
status: active
date_created: 2026-07-11
tags: [backgate, regime-v2, rollout, flip-ladder, oof, provenance]
---

# Design — Regime v2 Rollout: 트윈라이트 → OOF → 4단 사다리 (설계 전용, 코드 0)

DEMO/PAPER 가상계정. [[regime_factory_2026-07-10]] R1 수렴 위에 얹기 — 재발명 금지.
전제: **capital-exposure 수술 완료** (R1 빌드 큐 조건) 후 착공.

## W2 — 트윈라이트 (behavior 0)
- `classify_regime_v2()` + `regime_state.regime_v2` + `positions.entry_regime_v2`
  병기. `_safe_lookup` degrade 패턴 **복제 의무** (구 4라벨 경로 무접촉).
- 스키마 [R1-B1]: regime v2 ∥ frontgate 컬럼군 — 조율은 단일 설계 리뷰(충돌 회피),
  **apply는 컬럼군별 독립 커밋 2개**. regime 백필 실패가 frontgate 섀도우·ProbeContext
  를 블로킹하는 커플링 차단.

## W3 — OOF 채점 공장
- `regime_v2_score.py`: 잔차-R · pairwise AUC/KS · embargo · DSR. 순수함수 단위테스트.
- consumer-eligible 판정 = 데이터 축적량 (리프 N≥40 · 총 N≥120 · pair≥500 — R1 숫자
  그대로, 캘린더 기한 아님). 판정 주체 = 사람 + /debate (트래커는 카운트만).

## W4 — flip 사다리 4단 (순차·독립 검증·독립 롤백)
```
① AI 컨텍스트 (judge payload) → ② 러너 regime_mult_v2 → ③ 셀 v2_alignment_mult
→ ④ exit_tightness (섀도우→flip, 엑싯 독립 단계 — 충돌 조정 ②)
```
- **frozen control + provenance** [R1-B4, 최대 개정]: 각 단계 승격 채점은 flip 이전
  축적분(frozen baseline)만 사용. post-flip 행에 `ladder_stage` 스탬프 병기.
  ③셀 채점은 ② 활성 여부로 조건화 — ①→② 모집단 오염, ②→③ survivor bias 봉쇄.
- 관찰 창 = 재량 아닌 **데이터량 고정**: 단계별 신규 리프 N≥40 + 2연속 워크포워드 창.
- 각 flip 전 R2 /debate (히스테리시스·churn·러너 5축 편입·셀 임계) + fresh sub-agent
  설계 리뷰 의무.

## 폴백 체인 (전 소비자 공유)
6상태 → 방향-only → 구 4라벨 → 글로벌. 라벨 오류 1건이 AI·러너·셀·엑싯 4소비자
동시 전파되는 최대 리스크의 완화 = 사다리 + 관찰 창 + 단계별 독립 롤백 + 이 체인.

## W5 — HMM 경쟁 분류기 트랙 (조건부)
v2 인프라(OOF 공장·섀도우 컬럼) 공유하는 경쟁 트랙 — v2 승격과 독립, 전용 게이트.

## 검증
- W2: 라이브 거동 diff 0 + 구 오픈 포지션 close 회귀 테스트.
- W4: flip 전후 A/B delta + 4라벨 귀속 무열화 (R1 조건) + 단계 간 관찰 창 준수.

실코드 근거: `polaris/core/live_recalc/regime_flip.py` · `polaris/core/regime_fit.py`.
관련: [[master-sequence]] · [[design-exit-matrix]] · [[design-brain-ai]] · [[design-monitoring]]
