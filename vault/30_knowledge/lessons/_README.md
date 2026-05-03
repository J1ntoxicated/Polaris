---
entity_type: index
entity_id: lessons_readme
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INDEX]]", "[[_INHERIT_QUEUE]]"]
mode: meta
reviewed_by: codex
tags: [meta, lessons, polaris]
---

# Lessons — 모태 5 핵심 lesson 인수 + Polaris 신규

## 인수 큐 (Phase D 이후)

모태 `auto_invasion_mk1-main/tasks/lessons.md`에서 인수할 5 핵심 lesson (Codex 디베이트 3 식별):

| Lesson ID (모태) | 핵심 | Polaris LESSON ID |
|---|---|---|
| #78 | NULL cascade — numeric column NULL 금지. property-based test 필수. | LESSON-001 |
| #47 | Paper vs Live 행동 격차 = 치명적. promotion gate에 paper/live diff audit 명시. | LESSON-002 |
| #46 | Runtime verify 의무. import 통과 ≠ runtime 통과. | LESSON-003 |
| #45 | Grep-before-guess. 제안 전 기존 코드 확인 의무. | LESSON-004 |
| #44 | 소비자 grep 증거 없는 feature commit 금지. | LESSON-005 |

## Polaris 신규 lesson

Phase 2부터 운영 중 학습한 패턴은 신규 LESSON-NNN으로 작성. 템플릿: [[.templates/LESSON]].

### 작성 규칙

- 1 trigger 사건마다 max 1 lesson (메타 작업 한도)
- `expires: never` (영구 참고)
- 가능한 경우 lint/hook으로 자동 강제 명시
- 백링크 ≥ 2 (관련 INSIGHT/ADR/component)

## Lint

`tools/vault_lint.py`:
- 30_knowledge/lessons/ 노트 백링크 ≥ 2 미충족 fail
- frontmatter `reviewed_by` 누락 fail
