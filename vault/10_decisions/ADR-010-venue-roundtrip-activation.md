---
type: ADR
adr_id: ADR-010
status: active
date_created: 2026-05-28
tags: [adr, venue, roundtrip, demo, fills, p0, isolation]
related: [[ADR-003]], [[harness-collab-protocol]], [[layer-3-sizing-risk]], [[ADR-009]]
reviewed_by: codex(2-pass) + jin (blanket auth 2026-05-28)
---

# ADR-010 — Real Demo Round-Trip Activation

## Context

5-axis 검수가 P0 발견: production paper loop 이 **실제 demo 주문을 한 번도 안 보냈음**.
open (`_production_pipeline.py`) + close (`_production_close.py`) 둘 다 simulate-only,
`--real-roundtrip` flag 폐기. 결과 = `data/polaris.sqlite` 17,259 fills 전부 가짜
(simulate 산출물). PnL/learner 신호 오염, "8-layer 작동" 주장 실증 근거 부재.

## Decision

1. **real_roundtrip wire 복구** — `--real-roundtrip` 재배선, venue adapter
   `open_position`/`close_position` 실호출 (OKX SPOT demo + Capital CFD demo).
2. **새 db 격리** — 실주문은 `data/polaris_live.sqlite` 에 기록. 기존 가짜
   `polaris.sqlite` (17,259 fills) 와 물리 분리 (혼재 금지, codex P0 #1).
3. **orphan → risk_events** — confirm/persist 실패 시 (venue 엔 열렸는데 내부
   미기록) `risk_events` 강제 emit. adapter 예외 시 allocator reservation 해제,
   Capital `deal_id` 영속, `pnl_r` 정산 — codex P0 #2~5.

검증: builder TDD 618 green → codex 외부 review 가 green 코드에서 실주문 안전
P0 5건 포착 → 재수정 → codex 재review 7/7 resolved safe=yes → 627 green.
green ≠ safe, builder≠reviewer 실증 ([[ADR-009]], [[harness-collab-protocol]]).

## Consequences

- **라이브 Capital demo 증명**: `open`/`close` 실호출, 13 fills 실 `dealId`,
  fault 0 / orphan 0 / fence conflict 0.
- **OKX live gap**: 검증 창에서 시그널 미발화로 OKX 실경로 미확인 — backlog
  (발화 창 잡아 재증명 필요).
- **orphan scanner backlog**: venue open / 내부 미기록 주기 sweep 미구현 —
  현재는 round-trip 시점 risk_events emit 만.
- 기존 17,259 가짜 fills = forensic/dashboard 한정 (live db 격리로 신규 분석 무오염).
- aggressive bias 0 위반: 차단 추가 X, sizing chain ([[layer-3-sizing-risk]]) 불변.

## Sources
- 5-axis audit 2026-05-28 ([[2026-05-28_5axis_audit]])
- codex 외부 review 2-pass (실주문 안전 P0 5건 → 7/7 resolved)
- Jin blanket auth 2026-05-28
