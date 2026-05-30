# 게이트 아키텍처 — 통합 vs 스트림별 분리 (Jin 질문 답, judge panel wp3oilota)

**질문**: 현 8-gate 배치 타당한가? 통합 vs 스트림별 분리? 분리면 전부 독립?

## 권고 = OPTION A (score 8/10): 통합 파이프라인 유지 + 스트림별 정밀도는 **DATA 주입**(게이트 fork 아님)
- B(공통 인터페이스, 5): wrong granularity(게이트-클래스에서 분기하나 실제 divergence는 DATA 레벨). C(완전 colony 분리, 3): 불변 3벌 복제(divergence 위험)+learner 표본 1/3 dilution, 정밀도 이득 0.

## 핵심 근거
- **게이트 CONTROL LOGIC은 진짜 stream-agnostic**(검증: 8 게이트 전부 0 stream 분기). G1-G8 state machine·fail-mode split·model tiering·T4 사이징·exit-FSM·learner = 공유 정당.
- AI 감사 부정합(regime price-only·equity regime evidence 0·G4 crypto-shaped guards·Capital payload 버그·G7 thin exit)은 **게이트에 먹이는 DATA가 빠지거나 버그**인 것 — 게이트 배치 문제 아님.
- **실현**: 스트림별 변형 = **단일 StreamProfile**(resolve_stream에서 1회 resolve) → GateContext 주입 → payload builder가 읽음. 산발 `if product_class==` 금지.

## 공유 유지 (불변/학습 — 절대 fork X)
G5 T4 invariant core(9-stack/hard-MAX) · G8 reflector+learner network · exit-FSM math(Q9 widening) · G1-G8 orchestrator+lifecycle+fail-mode+model tiering · rotation math + regime 2-consecutive confirm gate.

## 스트림별 분리 (DATA, StreamProfile 주입)
- **regime evidence source** (equity 현재 0 → gap collector 추가, _GROUP_SOURCES equity 키)
- **G4 pre-entry guard set** (crypto-shaped FastPathContext → 스트림별: B=세션/롤오버, C=RTH/PDT/갭)
- **G7 session-FORCED-exit rail** = 유일하게 DECISION-level 분기 정당(B는 주말/세션마감 전 flatten, C는 RTH-end no-overnight; A는 하드 캘린더 엑싯 없음)
- G3/G6/G7 payload evidence blocks (regime 단일 price string로 flatten 말고 per-stream evidence 주입)
- capital pool + rotation = **이미 per-venue**(단일 shared evaluator 내 venue loop)

## Jin 직관 답: "전부 스트림 독립?"
→ **완전 독립(프로세스/파이프라인 colony) 아님.** 자본/rotation은 이미 per-venue, regime/guards/exit는 DATA로 스트림별, **게이트 골격·불변·학습은 공유**가 옳음(완전 분리는 비용만 큼). 단 **G7 session-forced-exit 1곳만 진짜 per-stream DECISION 분기**.

## Phased path (무중단, additive)
- **Phase 0**(구조 enabler, FIRST): StreamProfile를 GateContext에 thread + resolve 1회. A/B/C parity 검증(거동 동일).
- **Phase 1**(정밀도/cost 최고, equity unblock): equity regime evidence — `_GROUP_SOURCES` equity + gap collector(Stream C regime 0→채움).
- **Phase 2**(버그+진입 enrich): Capital FIX-1 payload 정합 + G4 FastPathContext 스트림별 guard.
- **Phase 3**(Jin #1 exit, 유일 DECISION 변경): G7 session-forced-exit rail(session_calendar 키).
- 순서: P0 트레이딩 버그(zero-fill/same-bar)는 이미 해결됨(독립). rotation(#11)과 병렬 가능.

## NEEDS_DEBATE = True (아키텍처) + Jin 결정
D1 통합 A 확정(vs colony C) · D2 G7 session-forced-exit(유일 topology) · D3 단일 StreamProfile 실현(산발 if 금지) · D4 universe focus per-stream quota(crypto가 48슬롯 독식, B/C starve — optional) · D5 B short-side(forward).
