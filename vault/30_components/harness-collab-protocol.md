---
type: component
component: harness-collab-protocol
status: active
date_created: 2026-05-28
date_updated: 2026-05-28
tags: [harness, collaboration, multi-agent, orchestration, builder-not-reviewer]
related: [[ADR-001]], [[ADR-003]]
---

# Harness Collaboration Protocol

Multi-agent 협업 글루. 메인 Claude = **orchestrator + synthesizer**. 실무는 sub-agent 위임 (context pollution 방지 + brain contribution). 개별 agent 역할은 `.claude/agents/*.md`, 본 문서는 이들을 묶는 상위 orchestration.

## Agent roster
| agent | 역할 | reviewer? |
|---|---|---|
| (main) | 위임 결정 · 결과 종합 · 영속 기록 트리거 | — |
| `code-implementer` | builder. TDD. **self-review 금지** | ✗ |
| `codex-debate-partner` | codex 외부 review/debate (max 5 round) | ✓ |
| `vault-curator` | brain (vault r·w, lint, ADR mint) | — |
| Explore / Plan / general-purpose | 탐색 · 설계 · 다단계 | — |

## Orchestration glue
- 메인은 raw read/search dump을 context에 두지 않는다 → 위임 후 **압축 보고만** 회수.
- 위임받은 agent는 필요 시 하위 agent spawn · advisor(codex) 호출 · skill · vault r·w · sequential-thinking 자유 소환.
- 병렬 + 유기적: 독립 작업은 동시 dispatch, 결과를 메인이 종합.

## Handoff triggers
- 5+ 파일 read / codebase-wide search → Explore · general-purpose
- 큰 wave 검수 → **5-axis 병렬** (technical / 4-axis policy / coherence / functional / live audit)
- 다단계 설계 → Plan · 리팩토링 → code-simplifier
- 신규 코드 / 거동 변경 → `code-implementer` build → `codex-debate-partner` review
- 거부 키워드 sweep hit / 9-stack·sizing 변경 / vault write 충돌 → 즉시 전담 agent
- 오염 신호 (Read 5+ / grep 100+ line / 동일 axis 반복) → 전환
- 단일 known target → 직접 처리 (위임 overhead 회피)

## Builder ≠ Reviewer
코드·spec·rule 작성 주체의 self-review 금지 (confirmation bias). 신규 작성 → codex 외부 review **의무**, codex 불가 시에만 별도 Claude reviewer fallback. 다른 brain만이 진짜 검증.

## Super-brain 4합주 (비-자명 결정)
vault read → sequential-thinking → codex debate → vault update. 4개 모두 거친 뒤 결정.

## Brain contribution (의무)
모든 wave 종료 시 vault append (lesson / digest / ADR / `_NOW.md` / `log.md`). sub-agent도 vault r·w 가능, parallel write 충돌은 자기 namespace draft 또는 메인 종합으로 회피.

## Format 규약 (consumer별)
- agent instruction (CLAUDE.md / agent / skill) = 간결 md + **XML 태그**(critical 블록) — 3사 권장, orchestration 표현 최적
- vault = md + YAML frontmatter + `[[wikilink]]` (Obsidian-native, XML/HTML 금지 → graph 보존)
- config/state = JSON / SQLite
- rendered report = HTML (dashboard only — 실제 렌더되는 유일 layer)
