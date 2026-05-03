---
entity_type: insight
entity_id: INSIGHT-NNN
auto: false
last_modified: YYYY-MM-DD
expires: YYYY-MM-DD       # 필수: 6개월 기본, 관련 ADR 적용 후 30일 또는 명시 superseded
editable: true
back_links: ["[[<관련 ADR/INSIGHT/component>]]", "[[<2번째 링크 — orphan 차단>]]"]
mode: forensic|alpha|dev|debate
reviewed_by: codex|jin
tags: [type/insight, status/active, scope/spot, priority/p1, polaris]
---

# INSIGHT-NNN — <짧은 제목>

> 한 문장 요약 — 발견 내용 + 영향.

## Context (어떤 작업 중 발견)

<발견 시점, 모드, 관련 작업>

## Evidence (정확한 증거)

- 파일/라인: `path/to/file.py:NNN`
- DB/state: `<테이블/필드/값>`
- 로그/에러: `<인용>`

## Root Cause (근본 원인)

<왜 이렇게 됐나 — 추측 X, 증거 기반>

## Impact (영향 범위)

- 직접: <영향 받는 코드/노트/결정>
- 간접: <연쇄 영향>

## Recommendation (다음 액션)

- [ ] <필수 액션>
- [ ] <권장 액션 + 만료>

## Related

- 관련 ADR: [[ADR-NNN]]
- 관련 component: [[40_components/<name>]]
- 관련 lesson: [[30_knowledge/lessons/<name>]]
