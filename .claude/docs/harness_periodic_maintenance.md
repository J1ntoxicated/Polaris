# Harness Self-Maintenance — 이벤트 기반 (시간 금지)

> Jin 2026-04-19 11:55 "시간으로 하지 말고 커밋 사이클 / 코드 변경 % 이런 이벤트 메트릭으로". `feedback_monitor_only_no_cron` 의 연장.

## 점검 3축

1. Agent/Skill 구조 타당성 (사용률 / 중복 / 누락 / deprecated)
2. 문서 길이 & 복잡성 (60/80줄 상한, 중복, stale)
3. 설계 원칙 (Harness 본분 / 하드코딩 0 / 북극성 / learner 활성)

## 이벤트 트리거 (event-driven metric)

| 트리거 | 조치 |
|---|---|
| **Commit ≥ 5** (이후 누적) | `harness-drift-detector` — canonical/docs/code drift 감사 |
| **Commit LOC 변경 ≥ 300** (single commit) | `dev-audit-advisor` + `dev-wire-guardian` 병렬 — 대규모 변경 integrity |
| **Agent 신설/삭제** | 즉시 `harness-mode.md` agent pool 표 동기 |
| **Memory feedback 추가/revoke** | 즉시 `CLAUDE.md` 영속 원칙 섹션 + mode 참조 동기 |
| **Agent 호출 오류** (subagent_type not found) | 즉시 archive/오타 식별 + 표 수정 |
| **Agent 누적 호출 20+ 중 미사용 agent 식별** | Pool review, archive 후보 |
| **Harness `Edit/Write` 직접 수행 감지** (본분 이탈) | 본인 alert → agent delegation 재조정 |
| **Monitor `ERROR_BURST`** | `ops-log-advisor` + `dev-wire-guardian` 병렬 조사 |
| **Monitor `ALERT`** | `/alert-triage` 즉시 |
| **Alert item 누적 ≥ 5 OPEN** | `harness-ipc-curator` — queue 정리 |
| **`harness-mode.md` / `CLAUDE.md` 수정 직후** | `harness-drift-detector` — 참조 정합 |
| **Context 80% 도달 감지** | handoff 작성 + 자율 `/clear` 준비 |
| **Bot restart ≥ 3 connect 에 효과 없음** | 구조 타당성 재고 (debate / codex) |

## 자율 권한 (Jin 회부 X)

- `.claude/**/*.md` 전체 추가/수정/삭제
- Agent pool 재구성 (신설/통합/폐기)
- memory `feedback_*.md` 추가 (삭제는 Jin)
- Handoff memory 갱신

## 검증 기준

| 원칙 | 측정 방법 |
|---|---|
| Harness 본분 (Jin partner + orchestrator) | 최근 N tool 중 `Edit invasion/` / `Bash sqlite3` 직접 비율 → 낮아야 |
| 하드코딩 0 (`feedback_document_philosophy`) | agent/mode docs 내 magic number grep |
| 북극성 (dampen/block 0) | `data/alert_emit.jsonl` northstar_violation count |
| Learner 자율 | `data/param_history.jsonl` 최근 N 변경 record |
| 3-세션 잔재 0 | `canonical_files` / `ops_mode` / `dev_mode` grep |
| Agent delegation 실사용 | Harness session 최근 `Agent(...)` 호출 빈도 |
| 버전 네이밍 0 | `v[0-9]_` grep |
| 한자 0 (`feedback_no_hanja`) | 한국어 범위 외 한자 grep |

## 기록

수행 시 `tasks/audit_log.md` 에 append:
```
## [ts] SELF-MAINT — trigger=<event> scope=<area>
- 변경: ...
- 근거: ...
- 후속: ...
```

## 시간 기반 주기 금지 (재확인)

❌ "주간" / "월간" / "일일" 언급 금지
❌ Cron / 주기 ScheduleWakeup 금지 (`feedback_monitor_only_no_cron`)
✅ 이벤트 발생 시점에 측정 + 조치
