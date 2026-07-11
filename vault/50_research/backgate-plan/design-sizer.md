---
type: research
status: active
date_created: 2026-07-11
tags: [backgate, sizer, g5, kelly, t4, calibration, metalabel]
---

# Design — G5 Sizer 소화 (설계 전용, 코드 0)

DEMO/PAPER 가상계정 · aggressive bias 보존 · 9-stack 봉쇄: 사이징 접점 = 기존 T4
continuous scalar(0.75–1.5) 값 설정 + Kelly p축(곱셈 체인 밖)만. 신규 산식 발명 금지.

## 접점 3개 (전부 기존 seam)
1. **#4 캘리브레이션 확률** → `kelly_or_cold_start(calibrated_p)` — Kelly p축(min-term
   cap)으로 착지. `not cold_start` 뒤에서만 적용. cap축은 곱셈 체인과 직교(9-stack 무관).
   충돌 조정 ①: brain/블루프린트 불변 ②("T4 scalar 값 설정만")의 자구 확장 —
   W4 flip /debate 안건으로 블루프린트 문서 개정 동반 의무.
2. **#7 뉴스 conviction** → frontgate **product 슬롯** — L5 clip 산식 강제 재사용,
   `raw_cont_preclip` 4번째 인자. 신규 곱셈 축 발명 금지 (미니 9-stack 차단).
3. **#10 메타라벨** → 블루프린트대로 T4 continuous scalar 값 설정 유지.

## 이중 가산 가드 (flip 게이트 필수 항목)
- **#7 2경로**: 같은 뉴스 신호가 product 슬롯(직접 폴드) + judge 컨텍스트(→SIZE_UP→
  judge_conviction) 양쪽으로 사이징에 닿음. flip 승격 리뷰에 news_scalar↔judge verdict
  상관 실측 필수 — 유의 시 폴드 1경로만 개방. [R1-B2] flip 후에도 news_scalar↔
  judge_conviction 상관을 monitoring B **상주 항목**으로 승격 (비선형 SIZE_UP 경로 감시).
- **#4↔#10 이중 폴드** [R1-B2]: 같은 entry-quality 근원이 Kelly p축(cap)과 T4 scalar
  축을 동시에 움직이는 경로 실재 — #10 flip 게이트에 calibrated_p↔meta_p 상관 실측
  추가, 유의 시 폴드 1경로만 개방 (조정 ③ 패턴 복제).
- **cell_mult↔frontgate 스칼라** (조정 ⑥): 상관 실측 전 어떤 frontgate 스칼라도
  continuous_scalar 체인 실배선 금지 — "측정 후 결정" = W4 공통 전제.

## #3 랭크 컴포짓
사이저 **무접촉** — judge payload 컨텍스트(G1 한정, brain 도메인)로만. → [[design-brain-ai]]

## 순서·전제
- W2: (frontgate W0 배선 위) 섀도우 병기만 — 사이징 거동 diff 0.
- W3: #4 Platt 섀도우 평가 (오프라인) · 상관 실측 데이터 축적.
- W4: 게이트 통과 항목만 독립 flip. 검증 = flip 전후 A/B 섀도우 delta 로그 +
  사이징 폴드 지점 byte-diff가 설계 지점 1곳뿐임을 확인.
- #4 선행 flip의 #10 라벨 영향: R1 기각 — 메타라벨 타깃은 R-정규화(pnl_r)로 사이징
  불변. 단 **캡 바인딩 구간 라벨 주석**을 #10 승격 체크 소항목으로 흡수.

실코드 근거: `polaris/core/sizing/{engine,kelly}.py` ·
`polaris/core/pipeline/agents/entry_sizer.py` · `core/learners/meta_label.py`.
관련: [[master-sequence]] · [[integration-blueprint]] · [[experiment-roadmap]] · [[design-monitoring]]
