---
description: Switch to /dev mode — code work, build, refactor, bug fix. Hard-exclusive base mode.
argument-hint: "[optional task description]"
---

# /dev — Development Mode

Hard-exclusive base mode (mutually exclusive with `/alpha`, `/forensic`).

## Activates
- code-implementer agent (TDD discipline, fresh Claude sub-agent review 의무)
- analyst agent (research/backtest)
- vault-curator (ADR mint Jin sign-off 후)
- All P0 skills available

## Discipline
- Substantial 작업 = Workflow 스크립트 오케스트레이션 기본 ([[harness-collab-protocol]])
- TDD: 테스트 먼저, 최소 구현, refactor
- 본인 작성 코드 review 금지 → fresh Claude reviewer sub-agent (Workflow review 단계 — no-dev-GPT, GPT/codex dev 리뷰 금지)
- ADR mint = Jin sign-off 필요 (vault-curator)
- 거부 키워드 도입 금지 (SSOT: CLAUDE.md rejection-keywords 블록)

## NOT for
- Live paper trade monitoring → use `/alpha`
- Incident investigation → use `/forensic`

Task: $ARGUMENTS
