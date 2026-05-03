---
entity_type: adr
entity_id: ADR-004
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[code_review_workflow]]", "[[ADR-003]]", "[[principles]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: authoritative
authoritative_basis: Jin 2026-05-03 mandate + codex-debate 합의
tags: [type/adr, status/applied, scope/spot, polaris]
---

# ADR-004 — Code Review Codex External (Jin Mandate)

## Status

- applied: 2026-05-03 (Jin 명시 mandate)

## Context

Jin 2026-05-03 mandate: **"코드 리뷰는 같은 에이전트가 하는거 아니고 최초 코드 작성 뒤에는 전부 코덱스한테 리뷰 받고 피드백 받고 수정하고 하는거로 하자."**

모태에서 단일 agent 작성+리뷰가 컨텍스트 폴루션 반복 원인 (M3 유기적 연결 부재 / 패턴 50% 일관성). Codex 외부 리뷰가 우리 진단의 4개 과오를 잡았듯, 코드도 같은 검증 필요.

## Decision

**모든 신규/수정 코드는 작성 agent ≠ 리뷰 agent. codex 외부 리뷰 → 피드백 → 수정 → 재리뷰 → 합의 사이클 의무.**

상세 워크플로는 [[code_review_workflow]] 참조. 핵심:
- code-implementer 작업 완료 (TDD pass + self-verify) → codex 외부 리뷰 (codex-debate-partner agent 또는 codex:rescue 스킬)
- max 3 라운드 합의 (ADR-003 프로토콜)
- 미합의 시 Jin escalation
- commit 메시지: `[reviewed-by: codex(N rounds)]`

### 본인 리뷰 금지

- code-implementer 본인 코드 리뷰 ❌
- vault-curator 본인 노트 리뷰 ❌
- forensic-investigator 본인 INSIGHT 리뷰 ❌
- 모든 self-review는 self-verify (verification-before-completion)이지 리뷰가 아님

### 예외

긴급 fix path ([[emergency_bypass]]) — bypass 후 24h 내 codex 사후 리뷰 의무.

## Consequences

### 긍정
- 작성자 사각지대 차단 (외부 시각)
- 컨텍스트 폴루션 재발 차단 (모태 패턴 단절)
- 4 contract / P1~P7 위반 사전 검출
- Property-based test 부재 같은 quality gap 발견

### 부정
- 코드 작성 사이클 길어짐 (codex 응답 시간 + 합의 라운드)
- Codex API cost
- 디베이트 결과 의견 차이 시 Jin 부담 증가

### Mitigations
- max 3 라운드로 무한 디베이트 차단
- 자명한 변경 (typo, 1-line)은 리뷰 생략 가능 (4 contract Validation Boundary 정의에 따라)
- Codex cost monitoring (`vault/50_runtime/codex_cost_log.md`)
- Jin escalation 빈도 추적 (월 ≥ 3회 시 운영 모델 재검토)

## Alternatives Considered

- **단일 agent 작성+리뷰**: 모태 패턴 = 폴루션 재발. 기각.
- **다른 agent 내부 리뷰 (예: vault-curator가 code-implementer 코드 리뷰)**: 같은 시스템 내부 검증 — 외부성 부족. 기각.
- **Gemini 리뷰**: codex보다 트레이딩 코드 검증 능력 미검증. 향후 codex 보완 옵션으로 검토. 현재 기각.

## Verification

- [ ] Phase 2 첫 컴포넌트 작성 시 codex 리뷰 사이클 작동 확인
- [ ] codex-debate-partner agent definition (Phase C) 코드 리뷰 routing 명시
- [ ] vault_lint reviewed-by codex 검사 (Phase B)
- [ ] commit 메시지 reviewed-by 표기 확인

## Lint 강제

`tools/vault_lint.py`:
- `40_components/<name>.md` `reviewed_by: codex` 미명시 + 마지막 코드 수정 후 24h 초과 → fail

## Related

- ADR-003 (Codex debate protocol — 3 라운드)
- code_review_workflow (구체 워크플로)
- principles P3 (Write Path), P4 (Validation Boundary)
- emergency_bypass (긴급 fix 예외)
