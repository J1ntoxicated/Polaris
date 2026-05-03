---
entity_type: adr
entity_id: ADR-005
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[operating_model]]", "[[ADR-002]]", "[[principles]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: authoritative
authoritative_basis: codex-debate 합의 + Jin ack
tags: [type/adr, status/applied, scope/harness, polaris]
---

# ADR-005 — Harness 4 Modes (DEV/ALPHA/FORENSIC/DEBATE)

## Status

- applied: 2026-05-03

## Context

모태에서 1,431 alert dirs + 자율 forensic loop + 매 commit 38% fix/regression = M4 메타 작업 무한 증식. 한 세션에서 코드 작성 + forensic + debate + alpha 검증 다 섞여서 컨텍스트 오염. 4 contract 미정의(L4)의 표상.

## Decision

**Polaris 하네스는 4 모드로 운영. 한 작업 세션 = 한 모드. 모드 변경 시 explicit transition. 모드 혼합 금지.**

### 4 모드

| 모드 | 트리거 | 활성 agent | Vault 동작 |
|---|---|---|---|
| **DEV** | 코드 작성/수정 | code-implementer | read 40_components → write code → write component note |
| **ALPHA** | 가설 검증/백테스트/페이퍼 | vault-curator | read 60_alpha → BACKTEST/PAPER 결과 → ADR provisional |
| **FORENSIC** | 운영 이상 추적 | forensic-investigator | read DB/logs → write 1 INSIGHT (메타 작업 한도) |
| **DEBATE** | 모르는 결정 / high-stakes | codex-debate-partner | read 관련 ADR/INSIGHT → write ADR provisional |

### 모드 전환 규칙

- 작업 시작 시 모드 명시 (예: "DEV 모드: OKX SPOT WS feed 함수 추가")
- 모드별 활성 agent만 사용. 다른 모드 agent invoke 시 `pre_agent.py` hook 차단
- 모드 변경 = 이전 모드 산출물 (component note / ADR / INSIGHT)을 vault에 closing 후 새 모드 진입
- 한 세션 내 모드 혼합 금지

### 자율 forensic loop 폐기

모태 자율 forensic (1,431 alert dirs)은 메타 작업 폭증의 주범. Polaris는:
- forensic 모드는 **Jin 또는 명시 트리거에만 발동**
- 한 forensic 세션 = max 1 INSIGHT 산출 (메타 한도)
- alert 누적 시 forensic 모드 발동 (수동 또는 cron, 단 alert ≥ 5 도달 시만)

### 예외

긴급 fix path ([[emergency_bypass]]) — 24h 내 사후 모드 정합성 확인 의무.

## Consequences

### 긍정
- 컨텍스트 오염 차단 (모드 분리)
- 메타 작업 한도 (M4 차단)
- 각 모드 책임 명확 → debug 시 빠른 routing

### 부정
- 모드 전환 오버헤드 (작업 시작 시 명시 + closing)
- 진짜 multi-domain 작업은 모드 분할 필요 (예: forensic → debate → dev 순차)

### Mitigations
- 모드 전환 자동화 (post_stop hook이 다음 모드 권장)
- multi-domain은 explicit phase 분할 (브레인스토밍 → writing-plans → implementation 패턴)

## Alternatives Considered

- **모드 없이 단일 운영**: 모태 패턴 = 폴루션 재발. 기각.
- **2 모드 (코드 + 메타)**: alpha vs forensic vs debate 책임 혼합. 기각.
- **6+ 모드**: agent도 4개로 압축한 마당에 모드 inflation. 기각.

## Verification

- [ ] Phase B `pre_agent.py` hook 모드 책임 매트릭스 검사 작동
- [ ] Phase C 4 agent definition에 mode integration 명시
- [ ] Phase 2 첫 컴포넌트 (DEV) + 첫 가설 (ALPHA) 모드 분리 작동 확인

## Related

- ADR-002 (Vault-first architecture)
- operating_model §1 (4 모드)
- principles P3 (Write Path)
- emergency_bypass
