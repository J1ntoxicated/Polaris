---
entity_type: index
entity_id: vault_readme
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INDEX]]", "[[_NOW]]"]
mode: meta
reviewed_by: jin
tags: [meta, polaris, mode/meta]
---

# Polaris Vault — Obsidian Knowledge Hub

> **세션 시작 시 [[_NOW]] → [[INDEX]] 순으로 read.**

## 구조

7 계층 (자세히는 [[INDEX]]):
- `00_now/` — live diagnostic
- `10_constitution/` — 영속 원칙 + 4 contract (Jin only)
- `20_decisions/` — ADR
- `30_knowledge/` — INSIGHT/lesson/pattern
- `40_components/` — 코드 1:1 매핑
- `50_runtime/` — daily log + audit (append-only)
- `60_alpha/` — 가설 검증 워크플로

`generated/` (gitignore) — 자동 생성 derived view.

## 운영 모델

- 7 영속 원칙 ([[principles]])
- 4 contract ([[4_contracts]])
- 4 모드 / 4 agent ([[operating_model]])
- 코드 리뷰 codex 외부 의무 ([[code_review_workflow]])

## 참조 (read-only, 모태)

`/Users/jinyoon/Projects/auto_invasion_mk1-main/vault/` — 패턴/구조 참고용. 콘텐츠 복사 X (자세히는 [[ADR-001]]).

## Lint

`python3 tools/vault_lint.py --karpathy` (orphan/stale/contradictions)
`python3 tools/vault_lint.py --polaris` (P1/P2/ADR-004/P6 contract)
