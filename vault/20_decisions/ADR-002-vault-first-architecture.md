---
entity_type: adr
entity_id: ADR-002
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles]]", "[[4_contracts]]", "[[ADR-001]]", "[[INSIGHT-001]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: authoritative
authoritative_basis: codex-debate 3 라운드 합의 + Jin ack
tags: [type/adr, status/applied, scope/vault, polaris]
---

# ADR-002 — Vault-first Architecture (v4 7계층 + 7 원칙)

## Status

- proposed: 2026-05-03
- provisional: 2026-05-03 (codex-debate 3 라운드)
- applied: 2026-05-03 (Jin ack)

## Context

모태 컨텍스트 폴루션 진단 (인과 5층):
```
L1 수익 안 남
L2 알파 미검증
L3 검증 체계 부재
L4 4 contract 미정의 (Authority/Lifecycle/Write/Validation)
L5 멀티-에이전트 토폴로지 조정비용
```

L4 풀어야 L3 가능, L3 있어야 L2 검증, L2 통해 L1 해결. → L4부터 풀자 = 4 contract 명시화.

## Decision

**Vault 7계층 + 7 영속 원칙 + 4 모드 + 4 agent 운영 모델 채택**.

### Vault 7계층
```
00_now/         _NOW.md (live diagnostic, dataview)
10_constitution/ 영속 원칙 + 4 contract (Jin only)
20_decisions/   ADR (provisional/applied/superseded/expired)
30_knowledge/   INSIGHT/lesson/pattern (백링크 ≥ 2)
40_components/  curated summary (코드 1:1, code-implementer)
50_runtime/     daily log + audit (append-only)
60_alpha/       HYPO/BACKTEST/PAPER/Promotion Gate

generated/components/ (gitignore, untracked)
```

### 7 영속 원칙
P1 Authority 분리 / P2 Lifecycle / P3 Write Path + Provisional ADR / P4 Validation Boundary / P5 Alpha-first KPI / P6 Pure Core + Imperative Shell / P7 Property-based Testing

### 4 모드 + 4 agent
[[operating_model]] 참조.

## Consequences

### 긍정
- 4 contract 명시로 L4 해결 → L3/L2/L1 차단 메커니즘 활성화
- vault SSOT가 Code/DB와 분리 (lessons #80 위반 차단)
- 매 작업이 vault-first cycle을 거침 → 결정의 연속성 + 검증 가능성

### 부정
- 6개월 후 vault 노트 비대화 위험 (Codex 시나리오 #1)
- MOC/Dataview가 메타 작업 증식기가 될 위험 (시나리오 #2)
- 4 agent 축소가 병목 가능성 (시나리오 #5)

### Mitigations
- 노트 max age + 신규 노트 추가 전 같은 주제 update 우선 규칙
- vault 품질은 derived metric (P5 Alpha-first KPI)
- agent 추가도 ADR 필수 (P2 Lifecycle 적용)

## Alternatives Considered

- **모태 vault 그대로 인수**: 1,785 md 폴루션 매개체. 기각.
- **vault 없이 코드 주석만**: M1 SSOT 다중화 재발. 기각.
- **DB 중심 + vault 부속**: vault가 sub-utility로 격하되어 Polaris 의도와 불일치. 기각.

## Codex Debate Summary

- 라운드 1: vault-first 위험 (lessons #80) → Authority 분리로 해결 (Code/DB SSOT vs Vault knowledge hub)
- 라운드 2: G1/G2/Jin 병목 3 보강 → v3
- 라운드 3: P6 Pure Core + P7 Property-based test 추가 + 빠진 소스 8개 식별 → v4

## Verification

- [x] vault 7계층 디렉토리 생성
- [x] 7 영속 원칙 노트 작성 ([[principles]])
- [x] 4 contract 노트 작성 ([[4_contracts]])
- [ ] vault_lint v4 적응 (Phase B)
- [ ] 4 hook 신설 (Phase B)
- [ ] 4 agent definition (Phase C)
- [ ] vault_lint --karpathy --report = 0 violation

## Rollback Path

- vault-first 자체가 폴루션 매개체로 작동 시 (Codex 시나리오 #1~#4):
  - vault를 derived view로 격하 (DB/코드가 SSOT) ← 이미 P1으로 분리됐으니 이 방향이 자연스러운 fallback
  - 영속 원칙 P1~P7 중 일부 폐기 검토 (codex-debate 3 라운드 후)

## Related

- ADR-001 (SPOT-first fresh start)
- ADR-003 (Codex debate protocol)
- ADR-005 (Harness 4 modes)
- INSIGHT-001 (모태 spot 누더기)
- principles, 4_contracts, operating_model
