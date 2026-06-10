---
description: Activate /debate overlay — codex external debate for trading-param/strategy/architecture decisions. On-demand, non-dev only. Stacks on top of any base mode.
argument-hint: "[topic for codex external debate]"
---

# /debate — Codex External Debate Overlay (on-demand, 비-dev 전용)

Overlay mode (additive on top of `/dev`, `/alpha`, or `/forensic`).

## Activates
- codex-debate-partner agent (codex external debate routing — 비-dev 전용)
- Max 5 rounds per topic (escalation 방지)
- Output: `vault/50_research/debates/<topic>_<date>.md`

## When to use
- Trading parameter change (sizing / cap / amplifier)
- Strategy switch (add / remove / weight 변경)
- Architecture 대규모 변경 (Layer redesign)
- Conflicting evidence → cross-verify

## Discipline
- 1 round 단정 금지 (`feedback_no_single_review_verdict`)
- 슈퍼 브레인 4 합주: vault read → sequential-thinking → debate → vault update (`feedback_reasoning_superbrain`)
- 5 round 도달 → Jin 결정 위임

## NOT for
- Dev 코드/spec 리뷰·디베이트 → fresh Claude sub-agent (Jin 2026-05-31 no-dev-GPT)
- Trivial code style → no overlay needed
- Bug fix small scope → `/dev` only

Topic: $ARGUMENTS
