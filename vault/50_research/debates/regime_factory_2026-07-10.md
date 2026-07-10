---
type: debate
status: converged-r1
date_created: 2026-07-10
participants: [claude-opus-conductor, gpt-5.5-codex]
tags: [regime, taxonomy, candidate-factory, shadow-migration]
---

# 레짐 세분화 + 이론 테스트 공장 (Jin 제안) — GPT R1 수렴

**Jin**: "chop 말고는 쓰는 게 거의 없는데 세분화하거나 이론 테스트할 방법 없나."
**증거**: entry_regime 분포 chop 216/bull 170/bear 40/crisis 16인데 레짐별 평균
pnl_r 0.019/0.004/0.026/-0.033 — **분류가 성과를 못 갈라냄**(변별력 0). 현
스냅샷 3그룹 전부 chop.

## GPT BREAKS (수용)
1. 원시 분리력 채점은 마이닝 취약 → **전략/심볼/베뉴 정규화 잔차-R**을
   out-of-fold로만 채점, embargo 워크포워드+DSR 시행 디플레이션.
   리프 유효조건 N≥40 + 기여 전략 ≥3, 미달 시 상위 풀링.
2. 9상태는 현 표본(베뉴당 200-450 청산)에 과함 → **6상태로 시작**
   (방향 up/down/flat × 변동성 normal/expansion), 스퀴즈는 분리력을
   증명할 때까지 메타데이터.
3. flip 게이트 = 유용성 증명: 섀도우 이중태깅 총 N≥120 + 승격 리프당
   N≥40 + 연속 2 워크포워드 창에서 out-of-fold 분리 양수 + 기존 4라벨
   귀속 무열화.
4. 레짐 고유 리스크 = **상관 파손**(라우팅·러너 폴드·judge·prior 동시
   오염) → 소비자 flip 단계화 필수.

## FINAL-SPEC
- 구 4라벨 = canonical 거동 키 유지. 섀도우 `regime_v2` 추가(v0 거동 변화 0).
- v2 = 6상태. 피처: ADX slope·수익률 추세·BB폭 백분위·ATR 백분위.
- 공장 채점: OOF 정규화 잔차-R 분리(가중 pairwise AUC/KS + 기대값 순위 안정).
- 승격: DSR-디플레이트 양수 + 2연속 창 순위 안정 → "consumer-eligible shadow".
- 소비자 flip 단계: ①AI 컨텍스트 → ②러너 귀속 → ③셀 라우팅 (각 단계 검증).
- 라우팅 계층 폴백: 6상태 → 방향-only → 구 4라벨 → 글로벌.
- 레짐은 차단 없음 — 라우팅/스칼라 컨텍스트만 (flow_not_block).

**빌드 큐**: capital-exposure 수술 뒤. flip 전 R2 디베이트(피처 히스테리시스·
라벨 churn 제어) 의무 — 섀도우 단계는 무위험이라 R1 수렴으로 착공 가능.
