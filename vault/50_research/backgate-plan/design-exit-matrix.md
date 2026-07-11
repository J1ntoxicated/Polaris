---
type: research
status: active
date_created: 2026-07-11
tags: [backgate, exit, g6, g7, probes, calibrator, trail]
---

# Design — G6/G7 Exit Matrix 소화 (설계 전용, 코드 0)

DEMO/PAPER 가상계정 · flow_not_block: 엑싯은 스케줄/타이트니스 조정만, 차단 0.

## A — 오프라인 캘리브레이터 (W2 착지, 최우선)
- `v_probe_outcomes` 리더 — [[ADR-012-probe-engine-tuning-log|ADR-012]] Slice2의
  미착수 shadow-compare 완성. 핫패스 미접촉, 순수 오프라인 채점.
- W3 첫 측정 → trail_only **버킷별 부분 오픈** = 엑싯 도메인 최초 behavior 변화 (W4).
  버킷 미달분은 섀도우 지속 (일괄 오픈 금지).

## B — 프로브 확장 (W2, behavior 0)
- RegimeFitProbe **observe attach**: composite lean 산출을 관찰 전용으로 병기.
- Bucket EVENT/CARRY 델타 섀도우: 스케줄 델타를 로그만, 적용 0.
- **이중 타이트닝 금지** (충돌 조정 ④): 기존 `exit_tightness` 직결(`_production_tick_mfe`)
  과 composite lean 동시 라이브 불가 — 대체 또는 병렬 SHADOW 중 택일.
- ProbeContext 확장 필드에 **버전 스탬프** 의무 (구 오픈 포지션 close 회귀 보장).

## B' — G4 프로브 threading (W5 조건부)
#6 VWAP/#9 Squeeze가 G4에 착륙한 뒤에만 ProbeContext 필드 threading. 전제 미충족 시
무기한 보류 = 정상 상태.

## C — regime v2 → exit_tightness (flip 사다리 ④)
- 사다리 4단 확정 (충돌 조정 ②): 엑싯은 ①AI 컨텍스트→②러너→③셀 뒤의 **독립 4단계**.
- 섀도우 컬럼(`_FIT_TABLE` v2 병기)은 W2 선착륙, 실 flip은 ① 이후 독립 순차 —
  단계 간 관찰 창 = 데이터량 고정 (신규 리프 N≥40 + 2연속 워크포워드 창) [R1-B4].
- 폴백 체인 공유: 6상태→방향-only→구4라벨→글로벌. → [[design-regime-v2-rollout]]

## 검증
- W2: 라이브 거동 diff 0 (섀도우 row만 증가) + 구 포지션 close 회귀 테스트.
- W3: 캘리브레이터 순수함수 단위테스트 + 수치가 원본 테이블 직접 집계와 일치.
- W4: flip 전후 A/B 섀도우 delta 로그 + 4라벨 귀속 무열화 (R1 조건).
- 소비자-선행 원칙: 신규 섀도우 표면(컬럼/델타 로그)마다 오프라인 리더
  (캘리브레이터/트래커)를 설계 시점에 명명 — W2↔W3 짝 강제.

실코드 근거: `polaris/core/regime_fit.py` · `polaris/core/probes/` ·
`polaris/core/live_recalc/regime_flip.py`.
관련: [[master-sequence]] · [[design-monitoring]] · [[experiment-roadmap]]
