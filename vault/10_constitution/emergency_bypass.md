---
entity_type: constitution
entity_id: emergency_bypass
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[4_contracts]]", "[[principles]]", "[[code_review_workflow]]"]
mode: meta
reviewed_by: codex
tags: [type/constitution, status/active, polaris, emergency]
---

# Emergency Bypass — 긴급 fix 우회 + 24h 사후 산출물 의무

> Codex 디베이트 3 라운드 잔여 5% gap (G1) — 긴급 bypass 조건 명문화.
> 모태 lessons #45 ("긴급 상황에서 verify 단축 금지") 직접 인용.

## 1. Bypass 발동 조건 (둘 중 하나)

### 조건 A — 봇 ALIVE 상태에서 P&L 손실 진행 중
- 활성 포지션이 손실 누적 상태
- 정상 boundary (test → component note → codex 리뷰 → commit) 거치는 시간 내 추가 손실 우려

### 조건 B — 데이터 무결성 즉시 훼손 위험
- DB corruption 진행 중
- 잘못된 write가 cascade하는 중 (예: 모태 lessons #78 NULL cascade)
- 반복 가능한 데이터 손실

### 조건 C — Jin 명시 (수동 트리거)
- `EMERGENCY=1` 환경변수 설정 + Jin 명시 발언

위 외 다른 상황은 bypass 금지 (예: "급해서", "테스트 귀찮아서" 등 금지).

## 2. Bypass 시 허용 단축

| 정상 단계 | bypass 시 |
|---|---|
| TDD (실패 테스트 → 코드) | 사후 24h 내 작성 의무 |
| 40_components 노트 갱신 | 사후 24h 내 의무 |
| codex 외부 리뷰 | 사후 24h 내 의무 |
| vault_lint 통과 | bypass 가능, 사후 24h 내 통과 의무 |
| pre_commit hook | `EMERGENCY=1` 환경변수로 우회 가능 |

**금지 (bypass 시에도)**:
- ❌ Constitution 변경 (Jin only 영속)
- ❌ ADR applied 상태 변경 (Jin ack 필수)
- ❌ verify 압축 (모태 lessons #45)

## 3. 24h 내 사후 산출물 (의무)

bypass 후 24시간 내에 모두 완료:

1. **Provisional ADR** — 무엇을 왜 어떻게 bypass했는지
   - `vault/20_decisions/ADR-NNN-emergency-<topic>.md` (`status: provisional`)
   - 정상 codex-debate 사이클은 사후 진행
2. **Component note 갱신** — 변경된 코드의 `40_components/<name>.md` update
3. **Lessons 신규 항목** — 무엇을 학습했는지
   - `vault/30_knowledge/lessons/LESSON-NNN-<topic>.md`
4. **vault_lint 통과** — 0 violation
5. **Codex 사후 리뷰** — 변경 diff에 대한 외부 리뷰 (정상 사이클)
6. **`vault/_NOW.md` update** — bypass 발생 + 사후 처리 상태

## 4. Bypass 추적

`vault/50_runtime/emergency_bypass_log.md` (자동 append):
```
| timestamp | trigger | who | bypassed steps | sufficient_followup_at | status |
```

## 5. 위반 시

- 24h 내 사후 산출물 미완성 → Jin escalation
- 반복 bypass (월 ≥ 3회) → 운영 모델 재검토 ADR 강제 (구조적 문제 시그널)

## 6. Hook 강제

`pre_commit.py`가 `EMERGENCY=1` 환경변수 감지 시:
- vault_lint warning 허용
- `vault/50_runtime/emergency_bypass_log.md`에 자동 기록
- 24h 후 follow-up 미완료 시 다음 commit에서 차단
