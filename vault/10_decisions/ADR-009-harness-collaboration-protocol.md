---
type: ADR
adr_id: ADR-009
status: active
date_created: 2026-05-28
tags: [adr, harness, collaboration, multi-agent, orchestration]
related: [[harness-collab-protocol]], [[ADR-003]], [[ADR-001]]
reviewed_by: codex + jin (blanket auth 2026-05-28)
---

# ADR-009 — Harness Collaboration Protocol

## Context

Jin 의 multi-agent 협업 모델: 메인 Claude = orchestrator + synthesizer, 실무는 sub-agent 위임. 문제 = **context pollution** — 메인이 raw read/search dump 을 직접 흡수하면 context 오염 + brain contribution 누락. 개별 agent 역할(`.claude/agents/*.md`)은 있으나 이들을 묶는 상위 orchestration glue 가 canonical 로 부재.

## Decision

협업 모델을 3 layer 에 영속 설치 (Jin blanket auth 2026-05-28, ADR mint 포함 전부 자동 진행 승인):

1. **canonical spec** — 신규 [[harness-collab-protocol]] ([[ADR-003]] 와 동급 component): agent roster · orchestration glue · handoff triggers · brain contribution 의무.
2. **CLAUDE.md** — Handoff/Agent + Absolute mandates 를 XML 태그로 재구성 (critical 블록 명시화, 3사 권장).
3. **`.claude/settings.json`** — `polaris.collaboration` 블록 (config-as-state).

**Format 전략 (consumer 별)**: agent instruction = md + XML 태그 / vault = Obsidian md + `[[wikilink]]` (XML·HTML 금지, graph 보존) / config·state = JSON·SQLite / rendered report = HTML (dashboard only).

**Builder ≠ Reviewer**: 작성 주체 self-review 금지 (confirmation bias). 신규 작성 → codex 외부 review 의무. 본 buildout = codex 2회 통과 (builder≠reviewer 일관성 + XML 적정성 확인).

**Super-brain 4 합주** (비-자명 결정): vault read → sequential-thinking → codex debate → vault update.

## Consequences

- 메인 context 오염 ↓, sub-agent 압축 보고만 회수 → 긴 wave 지속 가능.
- 모든 wave 종료 시 brain contribution (vault append) 의무화 → 영속 학습 보존.
- Format 분리로 vault graph 무결성 유지 (XML 침투 차단).
- 검증 비용 ↑ (builder≠reviewer 강제) 그러나 confirmation bias 차단으로 순이득.
- 새 sub-agent 추가 시 roster + handoff trigger 갱신 필요 ([[harness-collab-protocol]] SSOT).

## Sources
- Jin multi-agent 협업 모델 + blanket auth 2026-05-28
- codex 외부 review 2회 (builder≠reviewer · XML 적정성)
- [[harness-collab-protocol]] canonical spec
