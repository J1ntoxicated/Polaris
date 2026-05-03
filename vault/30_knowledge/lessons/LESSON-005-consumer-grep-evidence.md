---
entity_type: lesson
entity_id: LESSON-005
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[LESSON-004]]", "[[INSIGHT-008]]", "[[code_review_workflow]]"]
mode: meta
reviewed_by: codex
maturity: authoritative
authoritative_basis: 모태 lessons #44 패턴 (consumer grep 미실행 → wire 누락 cascade)
tags: [type/lesson, status/active, scope/spot, polaris]
---

# LESSON-005 — 소비자 Grep 증거 없는 Feature Commit 금지 (모태 lessons #44 인수)

## Trigger (모태 사건)

신규 feature/함수 wire 후 모든 consumer (caller) path 검증 없이 commit → wire 누락 cascade. INSIGHT-008 (taker fallback unwired)이 직접 사례:
- `taker fallback path not yet wired (Phase 3)` stub commit
- consumer (router_spot.py) grep 안 함
- prod fire → 57/58 abandoned

## Rule

**신규 함수/wire commit 전 `grep -rn "<func_name>" src/ tests/` 로 모든 caller 검증. caller가 stub이면 stub은 fail-fast.**

## Why

함수 작성 ≠ wire 정착. 함수 작성 후:
- 기존 caller가 새 함수를 호출하는지
- 새 함수의 반환값을 caller가 정확히 처리하는지
- stub인 경우 caller에서 명시 fail (silent skip 금지)

검증 없으면 wire 누락 silent → prod fire.

## How to Apply (Polaris)

### code-implementer (DEV 모드) 작업 후 의무
1. 신규 함수 commit 전: `grep -rn "<new_func>" src/ tests/` → 모든 caller list
2. 각 caller가 새 함수의 시그니처 + 반환 정확히 처리 확인
3. stub 함수는 명시 raise/exit (silent return 금지)
4. property-based test (P7): stub branch 진입 시 fail 강제

### codex 외부 리뷰 (ADR-004) 검토 항목
- "변경된 함수의 모든 caller가 검증됐는가?"
- "stub은 fail-fast인가?"

### LESSON-004 (grep before guess)와 정합
- 신규 함수 작성 전 = 이미 존재 검증 (LESSON-004)
- 신규 함수 작성 후 = 모든 caller 검증 (LESSON-005)
- 두 lesson은 작업 cycle 양쪽 끝에서 동시 적용

## Related
- LESSON-004 (grep before guess)
- INSIGHT-008 (taker fallback unwired — 직접 사례)
- code_review_workflow
- principles P4 (Validation Boundary)
