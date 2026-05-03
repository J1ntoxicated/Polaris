# Alert Squad — 전담반 조직

> Jin 2026-04-19 01:38 — "알럿 전담반 + 로그 전구간 + 참조 문서"

## 조직도 (Harness 산하 sub-role, 3-role 구조 불변)

```
            🟩 Harness
                │
         ┌──────┴──────┐
         │ Alert Squad │  ← Harness sub-role (sessions 분리 아님)
         └──────┬──────┘
    ┌──────────┼──────────┐
    ▼          ▼          ▼
 Detector   Router    Handler pool
 (in-bot)  (inline)  (skill/agent)
```

## 3 역할

| 역할 | 위치 | 책임 | 로그 |
|---|---|---|---|
| **Detector** | 🤖 봇 `invasion/ops/harness_alerter.py` | 7+ threshold 감지, alert 파일 emit | `data/alert_emit.jsonl` (신규, Dev task) |
| **Router** | 🟩 Harness inline (`/alert-triage`) | Monitor → category lookup → handler dispatch → item 생성 | `data/alert_route.jsonl` (신규) |
| **Handler pool** | 🟩 Harness inline skill/agent | category 별 분석 (codex-rescue / general-purpose / Ops SQL) → analysis → item SPEC'D | `data/alert_handler.jsonl` (신규) |

## Skill / Agent 매핑

카테고리 별 핸들러 → `.claude/docs/alert_routing.md` Routing Table 참조.

- Codex 필요 건 (HIGH arch) → `codex:codex-rescue`
- 코드베이스 탐색 → `Explore` agent
- 외부 리서치 → `/research` skill
- 디베이트 → `/debate` skill (Jin 승인 필요)

## 참조 문서

| 문서 | 용도 |
|---|---|
| `.claude/docs/alert_squad.md` (본 문서) | 조직도 + 역할 |
| `.claude/docs/alert_routing.md` | Category → Handler 매핑 표 |
| `.claude/docs/alert_lifecycle.md` | emit→route→handle→close 로그 체인 + schema |
| `.claude/docs/alert_verification.md` | 매일 health 체크리스트 (squad 멀쩡히 도는지) |
| `.claude/commands/alert-triage.md` | 수동/자동 triage slash command |
| `tasks/harness_items.md` | Active queue (OPEN/IN_PROGRESS/SPEC'D/CLOSED) |

## 북극성 정합

- **dampen/block 자동 spec** — `feedback_northstar_auto_fix` 위임대로 handler 가 Dev spec 바로 push, Jin 승인 skip
- **trade logic 변경** — 항상 human-in-loop (Jin 04-18 12:18 fsm_autorevert spec)
- **Squad 자체 운영** 은 Harness 단독, 공격량 삭감 금지 원칙 위반 시 handler 가 거부
