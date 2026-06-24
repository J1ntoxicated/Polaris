---
type: research
status: built-step1-reviewed-not-deployed
date_created: 2026-06-24
tags: [debate, architecture, realtime, quote-ticks, layer-0, sentinel]
---

# Tick-Stream / Verification-Engine Decoupling — Design (debate-validated)

## Problem (measured)
`quote_ticks` PK=(instrument_id,ts) appends a row per tick → 645k rows/215MB → ENOSPC + writer↔writer 락경합(1Hz INSERT vs 15s retention DELETE). 운영 핫패스(gate/exit/G4/P5)는 **이미 100% 인메모리 링** — 테이블 안 읽음. 테이블 실소비자 = RO 별도프로세스 2개: Dashboard(instrument당 최신 mid) + Sentinel(S1=venue MAX(ts) 신선도; S6=600s/300s COUNT+max flow_size).

## Debate verdicts (GPT codex + Gemini 2.5-pro, 2026-06-24)
- **D1 단일행 LWW=근본수정?** GPT 찬성(단 venue 차원 보존 필수) · Gemini 조건부(틱폐기 전제 경계, 아카이브 후속). → **채택**: 데이터모델 교정이 근본. 별도파일/IPC는 우회.
- **D2 in-mem bucket S6?** GPT 조건부(bucket경계 오차·WARMING 명시) · Gemini 기각(재시작 시 ~600s S6 블라인드). → **절충 채택**: 버킷 유지 + S6b는 durable `last_tick_ts`(재시작 즉시), S6a는 window<600s면 **WARMING**(silent OK/FAIL 금지). NULL/size합/grouping 의미는 테스트로 고정.
- **D3 별도파일 기각?** GPT 지금 기각OK(stage-2 옵션 보존) · Gemini 별도파일 주장(장애격리). → **절충**: 지금 X(단일행이 1차 경합 제거), 교차테이블 blocking 증거 시 `realtime.sqlite` stage-2.
- **D4 위험?** 양측 공통: **additive-first 마이그레이션**(blind DROP+CREATE 금지) · 비원자 2-테이블 skew · 미지의 소비자(adhoc/notebook/replay). → 채택.

## Final design
1. **quote_ticks → 단일행 LWW** (PK=instrument_id, venue 컬럼 보존). 무한증식·프룬DELETE·경합 source 동시 제거 → writer 1개 → 경합 구조적 불가. Dashboard/S1 동일결과(S1 검증: `MAX(ts) GROUP BY venue` on latest-per-instrument, 또는 `tick_inflow.last_tick_ts`로 이전).
2. **tick_inflow(venue PK, last_tick_ts, ticks_600s, max_flow_size_600s, window_started_at)** — writer venue별 인메모리 롤링버킷(10×60s) 1Hz UPSERT, quote LWW와 **같은 BEGIN;COMMIT**(skew 0, 추가 writer 0). S6b=`(now-last_tick_ts)>300`; S6a=`ticks_600s>=50 AND max_flow_size<=0` 단 window<600s면 WARMING.
3. **별도 realtime.sqlite = 안 함**(stage-2 문서화). 게이트 인메모리 = 이미 됨(변경0).
4. **마이그레이션 additive-first**: 신 스키마+tick_inflow 생성 → 신 Sentinel S6/Dashboard 배포 → 검증 → 구 quote_ticks 모양/retention rule 제거. 스키마버전 체크 + 구 reader fail-fast.
5. 불변: M1(행수↓ → stall개선)/M2(on_quote 인메모리)/단일writer/M6 monotonic/flow_not_block 보존. 거부키워드 0.

## Deferred (out of scope — 명시)
틱 히스토리 아카이브(백테스트/리플레이): 투기적·미요청·핫DB 부적합 → 필요 시 별도 COLD sink(압축로그), 이번 수정 게이트 아님. (Gemini 권고 일부는 보수 아키텍처/표본 논거라 DEMO·flow_not_block 맥락에선 가중치 낮춤; cold-start·skew·마이그레이션 순서만 기술적 실재 리스크로 채택.)

## Build (다음, thrash 금지)
TDD(스키마·venue카운터·S6재작성 각각 실패→코드→pass) → fresh Claude 리뷰 → additive 배포 → 검증 → 구 모양 제거. 관련 [[feedback_no_quick_patch_ever]]·[[feedback_per_ticker_tailored_gates]].
