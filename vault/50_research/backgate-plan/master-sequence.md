---
type: research
status: active
date_created: 2026-07-11
tags: [backgate, sequence, dependency-graph, waves, shadow, flip]
---

# Master Sequence — 의존성 그래프 + 웨이브 시퀀스 (설계 전용, 코드 0)

DEMO/PAPER 가상계정. 단일 리듬: **섀도우 병기 → 오프라인 채점 → 항목별 독립 flip**.

## 의존성 그래프
```
[W0 진행 중] frontgate top10 섀도우 배선 ─┬─ #4 캘리브레이션 → kelly calibrated_p (sizer)
                                          │      └(전제)→ #10 메타라벨 → T4 값설정 + G8 레슨
                                          │                   └→ ai_feedback 러너 (#4·#10 동시 승격 후)
                                          ├─ #7 뉴스 conviction → frontgate product 슬롯 (sizer)
                                          │                  └→ _frontgate_line 컨텍스트 (brain)
                                          ├─ #3 랭크 → judge payload 컨텍스트 (brain, G1 한정)
                                          ├─ #5 DFII10/#7 피드 → log_scan 신선도 마커 (monitoring)
                                          └─ #6 VWAP/#9 Squeeze (G4) → ProbeContext threading (exit)
[외부 전제] capital-exposure 수술 → regime v2 트윈라이트 (R1 빌드 큐)
regime v2 → OOF 채점 공장 → consumer-eligible → flip 사다리 4단:
    ①AI 컨텍스트 → ②러너 regime_mult_v2 → ③셀 v2_alignment_mult → ④exit_tightness
gate_shadow_events → monitoring A(채널 건강) → monitoring B(승격 트래커)
v_probe_outcomes → 오프라인 캘리브레이터 → trail_only 버킷별 부분 오픈 (최초 behavior 변화)
mini 판정 섀도우 카운트 → C-predicate → mini→5.5 2단 에스컬레이션 (후기)
스키마: regime v2 컬럼 ∥ frontgate 컬럼 → 단일 설계 리뷰, apply는 독립 커밋 2개 [R1-B1]
```

## 웨이브 시퀀스 (W0 = 현행 진행 웨이브)
- **W1 감시 그물 선행** (전제 없음, 즉시): monitor_tick ⑦ + log_scan 마커 + digest 롤업
  + 분포 가드/fingerprint [R1-B5]. 신규 write 표면 0. → [[design-monitoring]]
- **W2 섀도우 일제 확장** (behavior 0): (a) regime v2 트윈라이트 (capital-exposure 후)
  (b) exit 오프라인 캘리브레이터 + 프로브 observe (c) brain _frontgate_line + C-predicate
  카운트 (d) 스키마 마이그레이션 — 조율 단일 리뷰, apply 커밋 2개 분리 [R1-B1].
  검증: 라이브 거동 diff 0 + 구 포지션 close 회귀 + ProbeContext 버전 스탬프.
- **W3 오프라인 채점·승격 인프라**: regime_v2_score.py(잔차-R·pairwise AUC/KS·embargo·DSR)
  + promotion_tracker + exit 캘리브레이터 첫 측정 + #4 Platt 섀도우 평가.
  전제 = 데이터 축적량 (리프 N≥40·총 N≥120·pair≥500 — 로드맵/R1 숫자, 캘린더 아님).
- **W4 항목별 독립 flip** (최초 behavior 변화): 자기 게이트 통과분만 — #7 product 슬롯 /
  #4 calibrated_p / regime 사다리 ①→④ 순차 / exit trail_only 버킷. flip마다 /debate(R2)
  + fresh sub-agent 리뷰 + 상관 실측 + 토큰 예산 체크 [R1-B3]. **일괄 flip 금지.**
- **W5 후기 구조물** (조건부): ai_feedback 러너+G8 barrier (#4·#10 동시 승격 후) ·
  mini→5.5 실배선(발동율 상한+비용 캡 [R1-B3]) · Squeeze/VWAP threading · HMM 경쟁 트랙.
  전제 미충족 시 무기한 보류 = 정상 상태.

## 충돌 조정 (8건 확정)
① #4 착지 = sizer안(Kelly p축 cap, 곱셈 체인 밖) — W4 /debate서 블루프린트 자구 개정 동반
② flip 사다리 = 4단 (엑싯 ④ 독립) ③ 뉴스 2경로 → 상관 유의 시 폴드 1경로만 [B2 상시화]
④ RegimeFitProbe 이중 타이트닝 금지 ⑤ 스키마 = W2-d ⑥ cell_mult↔frontgate 상관 실측 전
실배선 금지 ⑦ 임계값 SSOT = vault 문서, 코드는 backlink 주석만 ⑧ Haiku 틱 = 고정 쿼리만.

## 리스크 톱3
1 레짐 라벨 오류 4소비자 동시 전파 → 4단 사다리+독립 롤백+폴백 체인(6상태→방향→구4라벨→글로벌)
2 조용한 이중 가산 → 상관 실측 게이트 + 폴드는 L5 clip 산식 단일 슬롯만 (신규 산식 발명 금지)
3 소비자 없는 섀도우 증식(paid no-op 재발) → 신규 표면마다 오프라인 리더 명명 (W2↔W3 짝 강제)

R1 수용 7 / 기각 2: [[backend_digestion_blueprint_r1_2026-07-11]] · 인덱스: [[_index]]
