---
entity_type: lesson
entity_id: LESSON-004
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles]]", "[[code_review_workflow]]", "[[forensic-investigator|.claude/agents/forensic-investigator]]"]
mode: meta
reviewed_by: codex
maturity: authoritative
authoritative_basis: 모태 lessons #45 (1h 안에 3회 게싱 위반 — MSG-109/110/111)
tags: [type/lesson, status/active, scope/spot, polaris]
---

# LESSON-004 — Grep-Before-Guess (모태 lessons #45 인수)

## Trigger (모태 사건, 2026-04-13)

같은 wake 1h 안에 3회 게싱 위반:
- MSG-109: 가설만으로 root cause jump → DB SQL refute
- MSG-110: "신규 `market_hours.py` 만들어야" → grep으로 이미 존재 발견
- MSG-111: "신규 `time_to_close()` 헬퍼 만들어야" → grep으로 `minutes_to_close()` 이미 존재 발견

= urgency 압박 시 self-discipline 결함 패턴.

## Rule

**신규 모듈/함수 제안 전 `grep -rn "module_name" src/` 1회 필수. 이미 존재 시 위치만 알려주기.**

## Why

추측 기반 제안은:
- 잘못된 root cause로 시간 낭비
- 중복 코드 생성 (이미 존재하는데)
- 같은 패턴 반복 → 신뢰도 하락

Urgency 압박 시 verify 단축 = 더 큰 시간 낭비.

## How to Apply (Polaris)

### code-implementer (DEV 모드) 작업 전
- 신규 함수/모듈 작성 전 `grep -rn "<name>" src/ tests/` 의무
- 이미 존재 시: 기존 위치 사용 또는 ADR로 변경 정당화

### forensic-investigator (FORENSIC 모드) recommendation 전
- root cause 결론 전 evidence 인용 의무 (file:line / DB query / log excerpt)
- 추측 → "Hypothesis: ..." 명시, 검증 후에만 "Root cause: ..."

### codex-debate-partner (DEBATE 모드) 결정 전
- 관련 ADR/INSIGHT 백링크 따라가기 (이미 결정된 사항인지)
- ADR proposed 7일 초과 시 자동 폐기 (P2)

### Urgency 시
- bypass 금지 — emergency_bypass 발동 시에도 24h 사후 verify 의무 ([[emergency_bypass]])
- "끝까지 처리" 원칙 — ACK = 검증 완료 약속 ([[LESSON-003]])

## Related
- principles P3 (Write Path)
- principles P4 (Validation Boundary)
- LESSON-003 (Runtime verify)
- LESSON-005 (소비자 grep 증거)
- code_review_workflow
