---
description: Activate /debate overlay — codex external review for architecture/strategy decisions. Stacks on top of any base mode.
argument-hint: "[topic for codex external review]"
---

# /debate — Codex External Review Overlay

Overlay mode (additive on top of `/dev`, `/alpha`, or `/forensic`).

## Activates
- codex-debate-partner agent (codex external review routing)
- Max 5 rounds per topic (escalation 방지)
- Output: `vault/50_research/debates/<topic>_<date>.md`

## When to use
- Trading parameter change (sizing / cap / amplifier)
- Strategy switch (add / remove / weight 변경)
- Architecture 대규모 변경 (Layer redesign)
- Conflicting evidence → cross-verify
- 모든 신규 코드 review (`feedback_code_review_codex_external`)

## Discipline
- 1 round 단정 금지 (`feedback_no_single_review_verdict`)
- Sequential-thinking + vault read + codex 4 합주 (`feedback_reasoning_superbrain`)
- 5 round 도달 → Jin 결정 위임

## NOT for
- Trivial code style → no overlay needed
- Bug fix small scope → `/dev` only

Topic: $ARGUMENTS
