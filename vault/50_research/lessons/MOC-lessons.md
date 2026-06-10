---
type: moc
status: active
date_created: 2026-06-11
tags: [moc, lessons, research, index]
related: [[MOC-A1-design-dev]], [[MOC-A3-raw-data]], [[INDEX]]
---

# MOC — Lessons (50_research/lessons)

활성 lesson = 인시던트/설계 교훈(사람 작성). 하단 legacy 시리즈 = 구 reflector가 vault에 쓰던 per-trade telemetry stub — 2026-06-03 ai_lessons DB(A3) SSOT 이관 후 전건 deprecated. 로우데이터 자체는 [[MOC-A3-raw-data]].

## 엑싯 / 청산 실행
- [[zombie_close_session_gate_wrong_predicate_2026-06-04]] — off-session 보호 부재: 세션 판정은 track별 calendar 분기 + builder self-review bias

## 회계 / PnL
- [[capital-pnl-cross-instrument-match_2026-06-04]] — Capital PnL 10^5x: entry-fill match에 instrument_id 필터 누락

## venue / API / 사이징
- [[capital_t4_lot_translation_bugc_2026-06-11]] — Capital T4→lot 변환 배선: margin ≠ exposure, 고정 $200 노출 종결
- [[t-p0-wire_2026-05-28]] — venue round-trip wire-miss: green ≠ safe, builder ≠ reviewer 실증

## 인시던트 / silent degradation
- [[gpt5_5-silent-gate-fail_2026-05-29]] — gpt-5.5 계약 drift → G6/G7/G8 99.97% 조용한 폴백, 모델 에러율로만 잡힌다

## 아키텍처 / 게이트 검증
- [[gate_unified_vs_perstream_2026-05-30]] — unified skeleton + per-stream decision module: split the data, share the mechanism

## Legacy reflector telemetry 시리즈 (전건 status: deprecated)
- t-flow: [[t-flow_2026-05-06]] [[t-flow_2026-05-07]] [[t-flow_2026-05-10]] [[t-flow_2026-05-25]] [[t-flow_2026-05-28]] [[t-flow_2026-05-29]] [[t-flow_2026-05-30]] [[t-flow_2026-05-31]] [[t-flow_2026-06-01]]
- t-p0: [[t-p0_2026-05-06]] [[t-p0_2026-05-07]] [[t-p0_2026-05-10]] [[t-p0_2026-05-25]] [[t-p0_2026-05-28]] [[t-p0_2026-05-29]] [[t-p0_2026-05-30]] [[t-p0_2026-05-31]] [[t-p0_2026-06-01]]
- t-p1: [[t-p1_2026-05-06]] [[t-p1_2026-05-07]] [[t-p1_2026-05-10]] [[t-p1_2026-05-25]] [[t-p1_2026-05-28]] [[t-p1_2026-05-29]] [[t-p1_2026-05-30]] [[t-p1_2026-05-31]] [[t-p1_2026-06-01]]
- t1: [[t1_2026-05-07]] [[t1_2026-05-10]] [[t1_2026-05-25]] [[t1_2026-05-28]] [[t1_2026-05-29]] [[t1_2026-05-30]] [[t1_2026-05-31]] [[t1_2026-06-01]]
- t_close: [[t_close_2026-05-07]] [[t_close_2026-05-10]] [[t_close_2026-05-25]] [[t_close_2026-05-28]] [[t_close_2026-05-29]] [[t_close_2026-05-30]] [[t_close_2026-05-31]] [[t_close_2026-06-01]]
- t_l: [[t_l_2026-05-07]] [[t_l_2026-05-10]] [[t_l_2026-05-25]] [[t_l_2026-05-28]] [[t_l_2026-05-29]] [[t_l_2026-05-30]] [[t_l_2026-05-31]] [[t_l_2026-06-01]]
- t_pnlr: [[t_pnlr_2026-05-28]] [[t_pnlr_2026-05-29]] [[t_pnlr_2026-05-30]] [[t_pnlr_2026-05-31]] [[t_pnlr_2026-06-01]]
- t_rt: [[t_rt_2026-05-28]] [[t_rt_2026-05-29]] [[t_rt_2026-05-30]] [[t_rt_2026-05-31]] [[t_rt_2026-06-01]]
- t_sim: [[t_sim_2026-05-28]] [[t_sim_2026-05-29]] [[t_sim_2026-05-30]] [[t_sim_2026-05-31]] [[t_sim_2026-06-01]]
- s3: [[s3_2026-05-29]] [[s3_2026-05-30]] [[s3_2026-05-31]] [[s3_2026-06-01]]
- 단발: [[t_fault_2026-05-07]] [[smoke-trade-1_2026-05-06]] [[py-clamp-1_2026-05-28]]

## 축 연결
- [[MOC-A1-design-dev]] 설계/개발 허브 · [[MOC-A2-bot-ops]] 봇 운영 · [[MOC-A3-raw-data]] telemetry SSOT(DB)
