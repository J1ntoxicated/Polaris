---
entity_type: lesson
entity_id: LESSON-003
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles]]", "[[code_review_workflow]]", "[[40_components/_README]]"]
mode: meta
reviewed_by: codex
maturity: authoritative
authoritative_basis: 모태 lessons #46 (MSG-114 dashboard regression — Jin "매번 이래")
tags: [type/lesson, status/active, scope/spot, polaris]
---

# LESSON-003 — Runtime Verify 의무 (모태 lessons #46 인수)

## Trigger (모태 사건, 2026-04-13)

Dev MSG-091 commit 후 Harness ACK + restart 만 하고 runtime verify 안 함 → operations dashboard down 상태 Jin이 발견. AST/import smoke만으로는 부족 (정의 없는 변수 사용은 NameError가 import 시점에 안 잡힘).

**반복 패턴**:
- MSG-067 regression-2 (hour_rows undefined)
- MSG-079 Phase 2 regressions 2회 (writer + downstream variable)
- MSG-114 dashboard `_mkt_closed` undefined

## Rule

**Import 통과 ≠ Runtime 통과. 코드 변경 후 runtime critical path verify 의무.**

## Why

Python은 dynamic. Import 시 NameError/AttributeError가 안 잡힘 — 실제 함수 호출 시점에 발견. Test가 없으면 prod fire.

## How to Apply (Polaris)

### code-implementer (DEV 모드) 작업 후 의무
1. **Dashboard render test** (변경 시): `python3 -c "from src.spot.<X> import render; render(<minimum args>)"` 또는 직접 launch + 5s 안 crash 확인
2. **Bot restart + 60s ERROR/Traceback grep**: ERROR count만 확인 X, 실제 traceback grep
3. **변경 함수 minimum 1 호출 직접 시도**: 특히 schema/field 변경 시
4. **Verify FAILED → 즉시 [URGENT-REGRESSION]** + commit 보류

### TDD (P4 + P7)
- 실패 테스트 작성 → 코드 → 통과 (단순 import test 금지)
- Property-based test 추가 (P7)
- self-verify (`superpowers:verification-before-completion`)

### code_review_workflow (ADR-004) 정합
- code-implementer self-verify 통과 → codex 외부 리뷰
- "끝까지 처리" 원칙 — codex 합의 = "검증 완료" 약속

## Related
- principles P4 (Validation Boundary)
- principles P7 (Property-based test)
- code_review_workflow (codex 외부 리뷰)
- 40_components/_README (component note 의무)
