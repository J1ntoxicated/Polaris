---
entity_type: constitution
entity_id: code_review_workflow
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[operating_model]]", "[[principles]]", "[[ADR-004]]"]
mode: meta
reviewed_by: codex
maturity: authoritative
authoritative_basis: ADR-004, Jin 2026-05-03 mandate
tags: [type/constitution, status/active, polaris, code_review]
---

# Code Review Workflow — codex 외부 의무 사이클

> Jin 2026-05-03 mandate: **작성 agent ≠ 리뷰 agent. 모든 신규 코드는 codex 외부 리뷰 → 피드백 → 수정 → 재리뷰 → 합의 사이클.**

## Why

모태 auto_invasion에서 단일 agent 작성+리뷰가 컨텍스트 폴루션 반복 (M3 유기적 연결 부재 / 패턴 50% 일관성). Codex 디베이트 1·2·3 라운드에서 codex가 우리 진단의 4개 과오(alpha 미검증/M1~M4 = 4 contract 표상/lessons #80 vault=view/C·F 과소평가)를 잡았듯, 코드도 외부 검증 필수.

## Workflow (단계별)

### 1. code-implementer 작성
- TDD: 실패 테스트 작성 → 코드 작성 → 통과
- `40_components/<module>.md` 갱신 (curated summary, frontmatter `pure: true|false`)
- Property-based test 추가 (P7 적용 영역: cell score, parser, regime, sizing, NULL 처리)
- self-verify (`superpowers:verification-before-completion` 스킬)

### 2. codex 외부 리뷰 호출 (의무)
- code-implementer가 작업 완료 후 즉시 codex-debate-partner agent 호출
- 또는 `codex:rescue` 스킬 직접 호출
- **Input**:
  - 변경 파일 diff (git diff 또는 명시 변경 목록)
  - `40_components/<module>.md` curated summary
  - 관련 ADR/INSIGHT 목록 (백링크 따라가기)
- **Request**:
  - Red-team review
  - 어떤 폴루션 위험? (M1~M4 / P1~P7 위반 여부)
  - 빠진 edge case는?
  - Property-based test 커버 충분한가?
  - 4 contract 위반 여부 (Authority/Lifecycle/Write/Validation)

### 3. Codex 피드백 수신
- vault-curator가 피드백을 INSIGHT/lesson stub으로 수집 (즉시 vault 기록)
- code-implementer로 routing

### 4. 합의 사이클
- 피드백 검토 (필요 시 sequential thinking 5-10 thoughts)
- 수용 / 반박 / 추가 디베이트 결정
- 수정 적용 → codex 재리뷰
- max 3 라운드까지 진행
- 미합의 시 Jin escalation (`vault/50_runtime/codex_review_escalation_log.md` 자동 기록)

### 5. Curator + Lint
- vault-curator: 변경된 component 노트 final 갱신 + lessons 신규 항목 (피드백에서 학습한 것)
- `tools/vault_lint.py --karpathy` 통과 의무

### 6. Commit
- pre_commit hook이 lint 재검증
- commit 메시지 표준: `type(scope): summary [reviewed-by: codex(N rounds)]`
- 예: `feat(spot/ws_feed): pure parser + reconnect [reviewed-by: codex(2 rounds)]`

## 예외 — 긴급 fix path

[[emergency_bypass]] 적용. bypass 후 24h 내:
- codex 사후 리뷰 의무
- provisional ADR + lessons 신규 의무

## Lint 강제

`tools/vault_lint.py`가 다음 시 fail:
- `40_components/<name>.md` frontmatter `reviewed_by: codex` 미명시 + 마지막 코드 수정 후 24h 초과
- `pure` 필드 누락
- `code_path` 누락 또는 파일 부재

## 통계 추적

`vault/50_runtime/code_review_stats.md` 자동 갱신:
- 평균 라운드 수
- 합의 도달률 (vs Jin escalation 비율)
- 피드백 → INSIGHT 변환률
