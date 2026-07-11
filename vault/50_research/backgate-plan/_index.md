---
type: research
status: active
date_created: 2026-07-11
tags: [backgate, digestion, research-index, sizing, exit, regime, monitoring]
---

# Backgate Plan — 후방 소화계 대개편 설계 인덱스 (2026-07)

DEMO/PAPER 가상계정 · aggressive bias 보존 · 이번 웨이브 = **설계 전용, 코드 0**.
프론트게이트 척후병 계층([[integration-blueprint]] · [[experiment-roadmap]])과
레짐 v2 공장([[regime_factory_2026-07-10]] R1 수렴)이 낼 새 정보 — 캘리브레이션
확률·뉴스 conviction·랭크 컴포짓·메타라벨·6상태 레짐 — 를 후방 전체가 **기존
seam의 값 설정만으로** 소화하도록 진화시키는 5도메인 합성 설계.

## 핵심 수렴
신규 아키텍처 불요. 이미 존재하나 끊긴 배선(judge_conviction seam · L5 clip
산식 · ProbeContext · fallback_value 풀링 · gate_shadow_events · Q() 고정쿼리)을
**섀도우 병기 → 오프라인 채점 → 항목별 독립 flip** 단일 리듬으로 잇는다.
모든 접점 = T4 continuous scalar(0.75–1.5) 값 설정 또는 읽기전용 컨텍스트
(9-stack 봉쇄 · flow_not_block 불변) · W1–W4 신규 GPT 콜 0 · 캘린더 기한 없음
(데이터 축적량 기준 발동).

## 문서 지도
- [[master-sequence]] — **핵심**: 의존성 그래프 + W1–W5 웨이브 시퀀스 + 충돌 조정 + 리스크 톱3
- [[design-sizer]] — G5 사이저: #4 calibrated_p Kelly p축 · #7 뉴스 product 슬롯 · #10 메타라벨 T4
- [[design-exit-matrix]] — G6/G7 엑싯: 오프라인 캘리브레이터 · RegimeFitProbe · trail_only 버킷
- [[design-brain-ai]] — G8·러너·AI 유닛: _frontgate_line · 토큰 예산 · mini→5.5 에스컬레이션
- [[design-regime-v2-rollout]] — 레짐 v2 트윈라이트 · OOF 채점 공장 · 4단 flip 사다리
- [[design-monitoring]] — 감시 그물 A~E: monitor_tick ⑦ · 분포 가드 · promotion_tracker

## 디베이트 상태
codex R1 (gpt-5.5, xhigh): **골격 HOLDS, 개정 7건 수용 조건 APPROVE_WITH_AMENDMENTS**,
기각 2건 — [[backend_digestion_blueprint_r1_2026-07-11]]. 수용 7건은 각 설계 문서에 반영 완료.
W4 각 flip 전 R2 /debate 의무 (canon).

## 빌드 큐 (불변)
capital-exposure 수술 → regime v2 트윈라이트. W1 감시 그물 = 즉시 착수 가능.

관련: [[ADR-003-8-layer-architecture|ADR-003]] · [[ADR-012-probe-engine-tuning-log|ADR-012]] · [[layer-2-per-gate-pipeline]]
