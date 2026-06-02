---
type: charter
status: active
date_created: 2026-05-06
tags: [charter, coding]
---

# Polaris Coding Conventions

## Language + Tooling
- Python 3.13+ (current macOS Homebrew)
- Type hints mandatory (mypy strict)
- Async-first (asyncio for I/O, sync for pure compute)
- ruff format + ruff check (no black, no flake8)
- pytest + hypothesis (property-based for pure functions)

## Naming
- snake_case (variables, functions, modules)
- PascalCase (classes, dataclasses)
- SCREAMING_SNAKE (constants)
- gerund-form skill names (`running-paper-loop`, `signaling-strategies`)

## Module structure
- `polaris/core/` (P6 pure: no I/O, no side effects)
- `polaris/venues/`, `polaris/harness/` (Imperative shell: I/O, side effects)
- Pure → side effect via dependency injection

## File limits
- Python file ≤ 500 lines (split at logical boundary)
- Markdown file ≤ 60 lines (`feedback_md_max_60_lines_split` 규약)
- Skill SKILL.md ≤ 500 lines (progressive disclosure with ref/*.md)

## Magic numbers + ParamRegistry
- No magic numbers in plans / specs (`feedback_no_hardcode_in_plans`)
- All config via env / ParamRegistry / strategy metadata
- Constants in module-level UPPER_CASE only when frozen forever

## Error handling
- `try/except: pass` 절대 금지 (silent swallow)
- 모든 except = log_event + raise OR explicit fallback
- Boundary validation only (user input, external API)
- Internal trust + framework guarantees

## TDD (P0+ implementation)
- 실패 테스트 → 코드 → 통과 cycle
- Property-based tests for pure functions (P6)
- Smoke tests P0, full suite P1 후반

## Comments
- Default: no comments
- Add only when WHY non-obvious (hidden constraint, subtle invariant, workaround for bug)
- Don't explain WHAT (well-named identifiers do that)
- Don't reference current task / fix / callers

## Git
- Commit message: `type(scope): summary [reviewed-by: codex(N rounds)]`
- type: feat / fix / refactor / test / docs / chore
- 새 commit 만들기 (amend 금지 unless explicitly requested)
- No `--no-verify` / `--no-gpg-sign`

## Pre/Post-flight
- Pre: ruff format + check / mypy / pytest
- Post: vault lint (light tier) / commit

## Cross-ref
- `feedback_md_max_60_lines_split.md` (memory)
- `feedback_no_hardcode_in_plans.md` (memory)
- `feedback_no_quick_patch_ever.md` (memory)

## 관련
- [[karpathy-workflow]]
- [[ADR-001-vault-structure]]
- [[ADR-009-harness-collaboration-protocol]]
