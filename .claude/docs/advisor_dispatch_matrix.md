# Advisor Dispatch Matrix

Anthropic "Advisor Strategy" 정책 명시화. Harness(Opus 4.7) 가 advisor 를 언제/어느 상황에 escalate 할지 정책.

## 원칙
- **Selective**: executor(Sonnet=dev-coder/ops-executor)가 default, advisor 는 특정 trigger 시만
- **Tool 분리**: advisor 는 보고서만, executor 만 코드/config 변경
- **Shared context**: 모든 agent 가 `.claude/` + memory 공유

## Event → Advisor 매핑

| Trigger | Primary | Secondary | When |
|---|---|---|---|
| loss_streak ≥5 | ops-trade-forensic | ops-log-advisor | alert 도착 직후 |
| wr_1h <30% n≥10 | ops-log-advisor | ops-trade-forensic | 누적 배치 |
| dd_1h <-threshold | ops-log-advisor | ops-param-tuner | 즉시 |
| silent >1800s | dev-entry-gate-specialist | — | 즉시 |
| regime flip ≥5 1h | ops-regime-watcher | — | 즉시 |
| 신규 feature 구현 | dev-refactor-advisor | dev-wire-guardian | spec 작성 전 |
| config param 변경 | ops-param-tuner | — | pset write 전 |
| 800L+ 파일 split | dev-refactor-advisor | — | spec 작성 전 |
| import/wire 의심 | dev-wire-guardian | dev-entry-gate-specialist | commit 후 |
| 로그 품질 저하 | ops-log-quality-auditor | — | idle 감지 |
| 세션 boot | harness-structure-advisor | harness-drift-detector | 세션 시작 시 |
| 신규 exchange/ticker | ops-exchange-registry | — | cross-impact 시 |
| 30min batch | ops-log-advisor | — | 주기 |

## Executor Feedback Loop (자동화)

PostToolUse hook (settings.json):
- dev-coder Write/Edit invasion/**/*.py → smoke 자동 → 실패 시 재호출
- ops-executor live_config.json edit → ops-param-tuner 자동 검증

## 원칙 Escalation (수동)

다음 경우 Harness 가 **Opus 자체** reasoning 필요 — advisor 대신 inline 분석:
- 북극성 판정 불확실
- 복수 agent 결과 충돌
- Jin 전권 위임 범위 결정 (architecture / live account)

## 참조
- Anthropic blog: https://claude.com/blog/the-advisor-strategy
- 우리 pool: `.claude/agents/` (20개, T13 Phase 4 5 신규 추가 04-24)
- 세션 모드: `.claude/commands/harness-mode.md`
