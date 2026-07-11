---
type: debate
status: converged-r1
date_created: 2026-07-11
participants: [claude-fable-moderator, gpt-5.5-codex]
tags: [backend-digestion, master-blueprint, frontgate, regime-v2, shadow-flip]
---

# 후방 소화계 마스터 블루프린트 — codex R1 교차검증 (설계 전용·코드 0)

DEMO/PAPER 가상계정. BREAK 5축 전부 제기 → 수용 7 / 기각 2. 골격 HOLDS.

## 수용 (블루프린트 개정 반영 의무)

1. **[B1] W2 마이그레이션 실패 격리** — 조율(단일 설계 리뷰·스키마 충돌 회피)은
   유지하되 apply는 컬럼군별 독립 커밋 2개(regime v2 ∥ frontgate). regime 백필
   실패가 frontgate 섀도우·ProbeContext를 블로킹하지 않게.
2. **[B2] #7 post-flip 상관 상시화** — 사전 실측(조정 3)만으론 비선형 SIZE_UP
   경로 못 잡음. flip 후 news_scalar↔judge_conviction 상관을 monitoring B 상주
   항목으로 승격.
3. **[B2] #4↔#10 이중 폴드 게이트** — 같은 entry-quality 근원이 Kelly p축(cap)과
   T4 scalar 축을 동시 이동. #10 flip 게이트에 calibrated_p↔meta_p 상관 실측
   추가, 유의 시 폴드 1경로만 개방(조정 3 패턴 복제).
4. **[B3] 토큰 예산 명문화** — "신규 콜 0" ≠ 토큰 불변. judge payload 필드별
   token cap(_frontgate_line 1줄 계약 + rank/regime 컨텍스트 합산 상한) +
   payload diff 예산 + G8 레슨 barrier cap을 W4 flip 체크리스트에.
5. **[B3] 콜-0 주장 자구 수정** — "신규 GPT 콜 0"은 W1–W4 한정으로 재명시.
   W5 mini→5.5 에스컬레이션은 실제 콜 증가 경로 — 발동율 상한+비용 캡 게이트
   전제(5.5=기존 P1 티어라 원칙 위반 아님, 주장 범위 오류였음).
6. **[B4] 사다리 frozen control + provenance** — 각 단계 승격 채점은 해당 flip
   이전 축적분(frozen baseline)만 사용. post-flip 행에 ladder_stage 스탬프 병기
   → ③셀 채점을 ② 활성 여부로 조건화. 관찰 창 = 재량 아닌 데이터량 고정
   (단계별 신규 리프 N≥40 + 2연속 워크포워드 창, R1 숫자 재사용).
7. **[B5] 분포 가드 + input fingerprint** — W1 그물에 채널별 분포 요약(평균/
   표준편차/top-symbol 집중도/dedup율, 숫자만) 추가. gate_shadow_events에 입력
   스냅샷 해시 병기 + 실경로 동일 해시 스탬프 → 섀도우-실경로 분기 마커
   (monitoring A). promotion_tracker 직접 SELECT 대조는 1회성→주기 재검.

## 기각

- **[B1] "capital-exposure 전제 W2 누락"** — 원문 W2 전제에 명시 존재
  ("(a)는 capital-exposure 수술 완료, 나머지는 W1만"). 요약 전달 누락 아티팩트.
- **[B1] "#4 선행 flip이 #10 라벨 오염"** — 메타라벨 타깃은 R-정규화(pnl_r)로
  사이징 불변; 진입/청산 선택 자체는 #4가 안 바꿈. 단 캡 바인딩 구간 라벨
  주석 의무만 #10 승격 체크에 소항목 추가.

## VERDICT

골격(섀도우 병기→오프라인 채점→항목별 독립 flip 리듬, W1→W5 순서, regime v2
4단 사다리, 충돌 조정 1–4) = HOLDS. 위 7개 개정 반영 조건 R1 수렴 —
**APPROVE_WITH_AMENDMENTS**. W4 각 flip 전 R2 /debate 의무 유지(canon 불변).
빌드 큐 불변: capital-exposure 수술 → regime v2 트윈라이트 → W1 감시 그물 즉시.

관련: [[regime_factory_2026-07-10]] · [[integration-blueprint]] · [[experiment-roadmap]]
