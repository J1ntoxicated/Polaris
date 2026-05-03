---
entity_type: adr
entity_id: ADR-NNN
auto: false
last_modified: YYYY-MM-DD
expires: YYYY-MM-DD       # 필수: applied 후 superseded 또는 expired까지
editable: true            # Jin only edit
back_links: ["[[<관련 INSIGHT>]]", "[[<관련 component/원칙>]]"]
mode: debate|alpha|dev
reviewed_by: codex        # codex-debate 통과
ack_by: jin               # provisional → applied 시 Jin ack 필수
ack_at: YYYY-MM-DDTHH:MM
tags: [type/adr, status/proposed|provisional|applied|superseded|expired, scope/spot, polaris]
---

# ADR-NNN — <결정 제목>

> 한 문장 결정 요약.

## Status

- proposed: YYYY-MM-DD
- provisional: YYYY-MM-DD (codex-debate N rounds 합의)
- applied: YYYY-MM-DD (Jin ack)
- superseded: by [[ADR-MMM]] (있을 시)

## Context (왜 이 결정이 필요한가)

<문제 상황, 트리거, 관련 INSIGHT>

## Decision (구체 결정)

<무엇을 어떻게 하기로 했는가>

## Consequences (영향)

### 긍정
- <장점 1>

### 부정
- <단점/위험 1>

### Mitigations (위험 완화)
- <위험 대응 액션>

## Alternatives Considered

- A: <대안 + 기각 사유>
- B: <대안 + 기각 사유>

## Codex Debate Summary

- 라운드 1: <주요 논점 + 결과>
- 라운드 N: <합의 도달>

## Verification (적용 검증)

- [ ] <검증 단계 1>
- [ ] <검증 단계 2>

## Rollback Path (폐기/되돌리기)

<어떻게 되돌릴 수 있나>

## Related

- INSIGHT: [[INSIGHT-NNN]]
- 관련 원칙: [[principles#PN]]
- 관련 component: [[40_components/<name>]]
