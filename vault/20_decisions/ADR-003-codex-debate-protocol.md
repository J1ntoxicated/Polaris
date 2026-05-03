---
entity_type: adr
entity_id: ADR-003
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[ADR-004]]", "[[operating_model]]", "[[principles]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: authoritative
authoritative_basis: codex-debate 3 라운드 합의 + Jin ack
tags: [type/adr, status/applied, scope/external, polaris]
---

# ADR-003 — Codex Debate Protocol (max 3 라운드 합의)

## Status

- applied: 2026-05-03 (Jin ack)

## Context

리즈닝 슈퍼브레인 (P5 ~ operating_model §5)의 핵심 컴포넌트인 codex debate. 무한 디베이트 = 진행 멈춤 + cost 폭증. 한 라운드 = 합의 못 함. → max 3 라운드 + 미합의 escalation 룰 필요.

## Decision

**Codex debate는 max 3 라운드. 미합의 시 Jin escalation. 각 라운드는 명시 합의 % 보고.**

### 라운드 정의

**Round 1**: 1차 비판
- input: 우리 입장 (진단/방향/구현 등)
- request: red-team review, 4 contract 위반 / 폴루션 위험 / 빠진 위험
- output: codex 합의 % + 비판 리스트

**Round 2**: 보강 v2
- input: round 1 비판 모두 반영한 v2
- request: 잔여 gap + 추가 비판
- output: codex 합의 % + 잔여 gap

**Round 3**: 모태 직접 검증 또는 시뮬레이션
- input: v3 + 명확한 시나리오 (마찰 시뮬레이션, 모태 read 등)
- request: 실제 운영 가능성 + missing piece
- output: codex 100% 또는 명시 미합의 5개

### 미합의 처리

- 라운드 3 후 미합의 = Jin escalation
- escalation 시 Jin이 직접 결정 또는 4 라운드 진행 결정
- Polaris 진단/방향 디베이트는 95→100% 도달 (3 라운드 사례)

### 합의 % 측정

- 100% 합의: codex가 명시 "100% 합의"
- N% 합의 + 5% gap 명시: 그 gap을 v(N+1)에 보강
- 50% 이하: 진단 자체 재검토

## Consequences

### 긍정
- 무한 디베이트 차단 (cost 한계)
- 합의 % 측정으로 진행 상황 명확
- Jin escalation 룰로 단일 병목 (Jin) 부담 완화

### 부정
- 진짜 어려운 결정은 3 라운드로 부족할 수 있음
- codex 의견에 과도하게 의존할 위험

### Mitigations
- Jin escalation 룰로 어려운 결정 처리 가능
- codex 의견은 input일 뿐, 최종 결정은 Jin (P3 Constitution = Jin only, ADR ack = Jin)

## Codex Debate Summary

이 ADR 자체가 우리의 디베이트 프로토콜 정착 결과 (3 라운드, 95→100%, Jin ack).

## Verification

- [x] Polaris 진단 디베이트 사례 (3 라운드, 95→100%)
- [ ] code-implementer 코드 리뷰 디베이트 적용 (Phase 2)
- [ ] Jin escalation log 누적 (`vault/50_runtime/codex_escalation_log.md`)

## Related

- ADR-004 (Code review codex external — 이 프로토콜의 코드 리뷰 적용)
- operating_model §5 (리즈닝 슈퍼브레인)
- principles P3 (Provisional ADR)
