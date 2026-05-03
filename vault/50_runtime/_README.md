---
entity_type: index
entity_id: runtime_readme
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INDEX]]", "[[operating_model]]"]
mode: meta
reviewed_by: codex
tags: [meta, runtime, polaris]
---

# Runtime — Daily Log + Audit (Append-only)

## 목적

매일 운영 상태 + audit trail + 메트릭 누적. **append-only**, harness 자동 작성.

## 파일 종류

| 파일 패턴 | 내용 | 작성자 | 빈도 |
|---|---|---|---|
| `daily-YYYY-MM-DD.md` | 하루 운영 요약 (commit / agent invoke / 모드별 시간 / vault 갱신) | harness (post_stop hook) | 일 1 |
| `emergency_bypass_log.md` | 긴급 fix bypass 기록 | pre_commit hook | 발동 시 |
| `codex_review_stats.md` | 코드 리뷰 라운드 / 합의 도달률 / Jin escalation 빈도 | codex-debate-partner agent | commit 시 |
| `codex_escalation_log.md` | codex 디베이트 미합의 → Jin escalation 사례 | codex-debate-partner agent | escalation 시 |
| `codex_cost_log.md` | Codex API cost 누적 (월별) | codex-debate-partner agent | 호출 시 |
| `mttr_alpha_monthly.md` | MTTR-alpha 월별 trend | cron (Phase 4 이후) | 월 1 |
| `vault_lint_report-YYYY-MM-DD.md` | vault_lint 결과 | post_stop hook | 일 1 |

## .gitignore

- `daily-*.md`는 .gitignore 처리 (개발 로컬 로그)
- 핵심 audit (emergency_bypass_log, codex_*) 는 tracked

## 작성 규칙

- **append-only**: 기존 entry 수정 금지. 정정은 새 entry로.
- 시간순 (newest 위)
- 한 줄 entry (필요 시 multi-line marker 사용)
- frontmatter `editable: false` (auto write only)

## Lint 강제

`tools/vault_lint.py`:
- 50_runtime/ 노트 수정 (delete or in-place edit) detect 시 fail (append-only 위반)
- `editable: false` 노트의 manual edit detect warn
