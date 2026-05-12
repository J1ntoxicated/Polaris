---
description: Switch to /dev mode — code work, build, refactor, bug fix. Hard-exclusive base mode.
argument-hint: "[optional task description]"
---

# /dev — Development Mode

Hard-exclusive base mode (mutually exclusive with `/alpha`, `/forensic`).

## Activates
- code-implementer agent (TDD discipline, codex external review 의무)
- analyst agent (research/backtest)
- vault-curator (ADR mint Jin sign-off 후)
- All P0 skills available

## Discipline
- TDD: 테스트 먼저, 최소 구현, refactor
- 본인 작성 코드 review 금지 → codex-debate-partner dispatch
- ADR mint = Jin sign-off 필요 (vault-curator)
- 위반 키워드 도입 금지 (defensive / 12주 / regulatory cap 등)

## NOT for
- Live paper trade monitoring → use `/alpha`
- Incident investigation → use `/forensic`

Task: $ARGUMENTS
