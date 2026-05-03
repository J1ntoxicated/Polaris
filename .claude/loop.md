# 자율 개선 루프 (Unified Index)

## 🔴 VAULT MANDATORY (Jin 2026-04-26)
**모든 작업 vault 사용 의무**. SSOT: [../vault/_meta/vault_mandatory_protocol.md](../vault/_meta/vault_mandatory_protocol.md).
- 진입: `vault/_NOW.md` 의무 read
- 종료: INSIGHT/ADR/digest/_NOW 중 1+ write
- SQL: cookbook reference 또는 mcp__sqlite (raw bash sqlite3 ad-hoc 금지)
- Entity 링크 의무 (`[[ticker]]` `[[strategy]]` `[[regime]]` `[[exit_pattern]]`)

## 핵심 원칙
- 판단은 코드/데이터 직접 확인, 추측/가정 금지
- 전체 흐름 파악 후 행동, 패치 위 패치 금지
- 팩트/검증 논리만. 매 판단마다 근거 확인
- 간격은 스스로 판단 (이상 시 짧게, 안정 시 길게)
- loop.md 자체도 진화 대상 — 부족 시 자율 업데이트
- **모든 사고 흐름 vault 누적**: chat 휘발성 의존 = 위반

## 세션 구조 (통합, 2026-04-19)
단일 Harness 세션 + 15 advisor/executor pool. 구 3-세션 (Dev/Ops/Harness) 및 IPC 파일 폐기.

## 세션 부팅
- `/harness-mode` → [harness-mode.md](commands/harness-mode.md)

## 핵심 참조
- [north_star.md](docs/north_star.md) — 🌟 북극성 (Architectural Invariant)
- [canonical_files.md](docs/canonical_files.md) — 핵심 경로 SSOT
- [model_strategy.md](docs/model_strategy.md) — Opus 단일 + effort 가변
- [audit_framework.md](docs/audit_framework.md) — 감사 카탈로그
- [coding_conventions.md](docs/coding_conventions.md) — 코드 규약
- [hooks.md](docs/hooks.md) — Claude Code hooks
- [logging.md](docs/logging.md) — 로깅 원칙

## 작업 큐
- [tasks/harness_items.md](../tasks/harness_items.md) — 통합 ITEM 큐 (신규 기록 대상)
- [tasks/lessons.md](../tasks/lessons.md) — 과거 실수 pattern
- [tasks/audit_log.md](../tasks/audit_log.md) — 감사 결과 ledger

## 영속 원칙 (메모리 — trigger 시 read)
- feedback_aggressive_always_profit · feedback_loss_profit_asymmetry — 북극성
- feedback_no_defensive_param_dampen — dampen/reduce 금지 (mult<1.0 누적 위반)
- feedback_root_cause_evidence_based — 증거 기반, 게싱 금지
- feedback_md_max_60_lines_split — 60줄 상한 + 분리 + 상호 참조
- feedback_harness_design_principles — Anthropic 원칙 매핑
- 전체 인덱스: `.claude/agent-memory/harness/MEMORY.md`

## Archive
- `.claude/archive/3-session-deprecated/` — 구 Dev/Ops/Harness 3-세션 문서 + 명령어
- `tasks/archive/2026-04-20_*` — 구 IPC 파일 (dev_to_*, ops_to_*, harness_to_*)
