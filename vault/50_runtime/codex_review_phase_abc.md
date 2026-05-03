---
entity_type: review
entity_id: codex_review_phase_abc
auto: false
last_modified: 2026-05-03
expires: never
editable: false
back_links: ["[[ADR-004]]", "[[_NOW]]", "[[code_review_workflow]]"]
mode: meta
reviewed_by: codex
tags: [meta, review, polaris, mode/meta]
---

# Codex Review — Phase A/B/C Bootstrap Code

> ADR-004 적용 첫 사례. Phase A (vault SSOT) + Phase B (lint + 4 hooks + settings.json) + Phase C (4 agent definitions + _DEPRECATED 정리) 통합 외부 리뷰.

## Summary

| 라운드 | 합의 % | 결과 |
|---|---|---|
| Round 1 (초기 검토) | **80%** | 5 high-priority fix + 6 빠진 항목 식별 |
| Round 2 (fix 검증) | **93%** | 5 fix + git symlink 모두 PASS, 잔여 gap은 Phase 1+ plan |
| Round 3 | (불필요) | codex 평가: "95% 도달 위한 즉시 fix 없음" |

**최종 합의: 93%** — Phase 0 commit 진행 OK.

## Round 1 핵심 비판

| # | 항목 | 등급 |
|---|---|---|
| P1 | machine_state_leak 패턴 부족 (write_bytes/json.dump/ORM 미탐지) + 코드 블록 fence 부정확 | WARN |
| P2 | expires=null 미검출, last_modified 누락 ADR 조용히 skip | WARN |
| P3 | builtin agent hardcoding 취약, 모드 매트릭스 hook 강제 부재 | WARN |
| P4 | **pre_commit이 --karpathy만 실행, Polaris contract gate 누락** | FAIL |
| P6 | pure 필드 warn 적절 | PASS |
| P7 | **vault_lint 자체 Hypothesis test 전무** | FAIL |
| ADR-004 | 라운드 timeout 없음, 합의 % 51-99% 기준 모호 | WARN |
| ADR-005 | codex-debate-partner ALPHA/FORENSIC 트리거 모호 | WARN |

## Round 1 → Round 2 적용 Fix (5개 + git symlink)

### Fix 1 (P4 FAIL → PASS)
**`pre_commit.py`**: `--karpathy` → 인수 없이 실행 (full lint, Polaris contract 포함). EMERGENCY=1 시 EMERGENCY_REASON 빈 값 차단.

### Fix 2 (P1 WARN → PASS)
**`vault_lint.py` `lint_machine_state_leak()`**:
- 패턴 추가: `write_bytes`, `json.dump`, `pickle.dump`, `shelve.open`, `DELETE FROM`, `executemany`, `session.add/commit`, `db.commit`
- `_code_block_ranges()` 함수로 fence 라인 단위 정확 매칭
- 다중 위반 모두 보고 (`break` 제거, `seen_patterns` set)

### Fix 3 (parser bug → PASS)
**`vault_lint.py` `_parse_frontmatter()`**: multi-line list (`- item`) 지원 + null/none/~ → 빈 문자열 coerce.

### Fix 4 (P2 WARN → PASS)
**`vault_lint.py` `lint_expires_required()`**: Fix 3 연쇄로 `expires: null` 정확히 검출 (FAIL 발동).

### Fix 5 (post_stop crash 방지 → PASS)
**`post_stop.py`**: `read_text()` OSError/UnicodeDecodeError try/except.

### 추가: git pre-commit symlink
`.git/hooks/pre-commit` → `../../.claude/hooks/pre_commit.py` (Claude Code settings.json hook 표준 PreCommit 미정의 → git native hook으로 대체).

## Round 2 잔여 Gap (Phase 1 이후 plan)

전부 Phase 0 commit 차단 요인 X. 추후 plan으로:

1. Dead reference check (`[[없는 노트]]`)
2. vault_lint_report 30일 자동 아카이브
3. ADR 번호 gap 검출
4. codex-debate-partner 라운드 timeout + 합의 % 51-99% 기준 명문화
5. forensic-investigator INSIGHT 한도 hook 강제
6. 모드 전환 승인 hook (FORENSIC → DEV)
7. vault_lint Hypothesis test suite (P7 자체 적용)
8. cost_log 자동 append hook
9. 백링크 cycle 탐지
10. 메타 작업 폭증 한도 hook

## Codex 평가 인용

> "Round 3 불필요. 95% 도달을 위한 남은 7% 중 즉시 구현 가능한 항목이 없음. 잔여 항목들은 구현 착수 전에 ADR 결정이 선행되어야 하며, Phase 0 commit 기준(핵심 lint + pre-commit + post-stop)은 모두 충족됨."

## ADR-003 적용

- max 3 라운드: 2라운드에서 95%-2% (93%) 도달, codex가 Round 3 불필요 판정 → 합의 stop
- Jin escalation 미발생

## ADR-004 적용 첫 사례

- 작성 agent (Polaris bootstrap, 즉 본 세션)
- 리뷰 agent: codex (외부, codex:codex-rescue agent)
- 라운드: 2
- 결과: 93% 합의
- 적용 변경: 5 fix + 1 추가
- commit 메시지: `[reviewed-by: codex(2 rounds)]`

## Related

- ADR-003 (Codex debate protocol)
- ADR-004 (Code review codex external — Jin mandate)
- code_review_workflow (워크플로 명문화)
