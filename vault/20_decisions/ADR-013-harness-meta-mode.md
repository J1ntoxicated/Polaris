---
entity_type: adr
entity_id: ADR-013
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[operating_model]]", "[[ADR-005]]", "[[code_review_workflow]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-04
maturity: provisional
tags: [type/adr, status/provisional, scope/harness, priority/p0, polaris]
---

# ADR-013 — HARNESS Meta Mode (메인 Claude = Orchestrator)

## Status
- proposed: 2026-05-04 (Jin 통찰 — "너가 모든거 다 오케스트레이션 해야하는데")
- provisional: 2026-05-04 (운영 모델 v2 정합)

## Context

기존 ADR-005 = 4 sub-mode (DEV/ALPHA/FORENSIC/DEBATE) — 한 작업 한 모드.
누락: 메인 Claude (사용자와 직접 소통) 자체의 mode 정의 없음.
사용자 mandate: "하네스 모드로 들어가고 나머지는 알아서 서브로 불러서 쓰면 되잖아".

## Decision

**메인 Claude = HARNESS 메타 모드 (5번째 모드, sub-mode 위)**.

### 5 모드 구조
```
HARNESS (메인 Claude — orchestrator, 항상 활성)
   ↓ dispatch
   ├── DEV (code-implementer subagent)
   ├── ALPHA (vault-curator subagent)
   ├── FORENSIC (forensic-investigator subagent)
   └── DEBATE (codex-debate-partner subagent)
```

### HARNESS 책임
1. **사용자 요청 분류** — 어느 sub-mode 적합?
2. **Sub-agent dispatch** — 적절한 specialist agent 호출
3. **Vault-first cycle 강제** — READ → seq thinking → codex → UPDATE
4. **결과 종합** — sub-agent output 읽고 사용자 보고
5. **운영 모델 감시** — sub-mode 누락 / vault 미작성 / codex 미리뷰 차단
6. **운영 인프라 (launchd)** — 자동 cron 관리 (별도, code execution X)

### HARNESS가 직접 하지 않는 것
- 코드 작성 (→ DEV dispatch)
- Vault 노트 직접 write (→ ALPHA dispatch)
- 운영 forensic 추적 (→ FORENSIC dispatch)
- High-stakes 결정 (→ DEBATE dispatch)

## Consequences

### 긍정
- 메인 Claude 책임 명확 (orchestrator only, executor X)
- Sub-agent 격리 → 컨텍스트 폴루션 차단 (M1~M4 root)
- 사용자와 소통 일관성 (메인이 모든 보고 종합)

### 부정
- Sub-agent dispatch 오버헤드 (token + 시간)
- 단순 작업도 dispatch → 작업 분할 비용

### Mitigations
- 자명한 작업은 "HARNESS direct" 허용 (예: 단순 status 보고, lint 실행)
- Sub-agent dispatch는 비-자명 결정에만 (4 모드 명시 트리거 조건)

## 위반 사례 인정 (자율 진행 중)

Phase 2c~e (HYPO-007~012, ADR-012, INSIGHT-018) 자율 진행 시:
- ❌ 메인 Claude가 코드 직접 작성 (DEV dispatch X)
- ❌ Vault 직접 write (ALPHA curator dispatch X)
- ❌ Codex 외부 리뷰 dispatch 안 함

→ 이 ADR-013 적용 후 모든 작업 sub-mode dispatch.

## Verification
- [x] operating_model.md §0 HARNESS Meta Mode 추가
- [ ] 향후 모든 코드 변경 → DEV dispatch (Agent code-implementer)
- [ ] 향후 모든 vault 작성 → ALPHA dispatch (Agent vault-curator)
- [ ] 향후 high-stakes 결정 → DEBATE dispatch (codex)
- [ ] FORENSIC 트리거 시 dispatch (forensic-investigator)

## Rollback Path
- HARNESS dispatch 오버헤드가 진행 마비 시 → "HARNESS direct" 허용 범위 확대 (별도 ADR)

## Related
- ADR-005 (Harness 4 modes — 본 ADR이 보강)
- operating_model v2 §0 + §1
- code_review_workflow (DEV → DEBATE codex review)
