# Claude Code Hooks (이벤트 드리븐 자동화)

Hook = Claude Code 런타임이 도구 이벤트 시 자동 실행하는 shell 명령. 세션 루프와 별개 이벤트 레이어.

## 현재 설치된 Hook (T13 Phase 4 MVP 포함, 3개)

- **PostToolUse (Edit|Write) `invasion/*.py`** — `import invasion.main` 자동 검증 (async 30s)
- **PostToolUse (Edit|Write) `*.md`** — 60줄 상한 초과 시 warn (`feedback_md_max_60_lines_split`, async 10s) [T13 Phase 4 설치]
- **PostToolUse (Edit|Write) `invasion/*.py`** — 3+자리 literal 존재 + `ParamRegistry`/`preg()` ref 없으면 magic-num warn (async 10s) [T13 Phase 4 설치]

## 향후 설치 후보 (Plan T13 v2.2 D12 이후)

| Hook | 이벤트 | 기대 효과 | 편입 단계 |
|------|--------|----------|----------|
| `PreToolUse (Edit|Write) doc magic-num reject` | Plan/tasks 문서 내 magic-num | `feedback_no_hardcode_in_plans` 강제 | D12 |
| `PostToolUse block-filter 검출` | `if ... : skip` 패턴 증가 warn | `feedback_flow_not_block` 보호 | D12 |
| `SessionStart auto-inject` | 세션 시작 | CLAUDE/MEMORY/T13_START_HERE 자동 load | D12 |
| `Stop / SubagentStop Gate 4축` | turn 종료 | Per-Change Gate 4축 자가 감사 로그 | D20 |

## Hook 관리 정책

- **추가/수정**: Harness 자율
- **검증**: 설치 후 실제 편집으로 테스트 (조용히 스킵하는 hook 방지)
- **위치**: `.claude/settings.local.json`
- **철학**: 이벤트 드리븐 강화 = 타임 폴링 대체

## 디자인 원칙

- Hook은 **신속** (30s 이내)
- 실패해도 **주 흐름 안 깨짐** (async, fallback)
- 로그 남김 (hook 자체 감사)

## 참조
- [settings.local.json](../settings.local.json)
- [loop.md](../loop.md) — 이벤트 드리븐 철학
- [harness-mode.md](../commands/harness-mode.md) — 통합 세션 운영 규약
