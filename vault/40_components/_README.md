---
entity_type: index
entity_id: components_readme
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INDEX]]", "[[principles]]", "[[code_review_workflow]]"]
mode: meta
reviewed_by: codex
tags: [meta, components, polaris]
---

# Components — Curated Summary (코드 1:1 매핑)

## 목적

Polaris 코드 모듈/클래스/주요 함수를 1:1로 매핑하는 curated summary. **자동 생성 X — code-implementer가 작성, codex 외부 리뷰 통과.**

## 자동 생성 vs Curated 구분

- `vault/40_components/` (이 디렉토리) — **curated summary, manual, tracked**
- `vault/generated/components/` — **자동 생성, untracked (gitignore)**, 로컬 detail 참조용

이 분리는 lessons #80 ("hourly auto-sync git objects 폭증") 차단 + Codex 권장 (delta-only + git-ignore).

## 작성 규칙 (P6 + ADR-004 강제)

- 템플릿: [[.templates/COMPONENT]]
- Frontmatter 필수:
  - `pure: true|false` (P6 분류)
  - `reviewed_by: codex` (모든 코드 변경 후)
  - `code_path: <path/to/module.py>`
  - `test_path: <path/to/test_module.py>`
- 백링크 ≥ 1 (관련 ADR 또는 원칙)

## 갱신 트리거

- 새 모듈/함수 작성 시
- 기존 함수 시그니처 변경 시
- 의존성 추가/제거 시
- 코드 리팩토링 시 (codex 리뷰 후)

## Lint 강제

`tools/vault_lint.py`:
- `40_components/<name>.md` `pure` 필드 누락 warn
- `reviewed_by: codex` 미명시 + 마지막 코드 수정 후 24h 초과 fail
- `code_path` 파일 부재 fail (코드 폐기 시 archived 디렉토리로 이동)

## generated/ 자동 생성 워크플로 (Phase 후속)

향후 도구 (Phase D writing-plans 결과):
- 코드 변경 감지 (post_edit hook) → `vault/generated/components/<name>.md` delta update
- 자동 생성 정보: 시그니처 / docstring / dependency graph / test coverage
- curated summary는 이 generated 정보를 참고해서 manual 작성
