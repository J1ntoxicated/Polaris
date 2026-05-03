---
entity_type: constitution
entity_id: governance
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles]]", "[[4_contracts]]", "[[INDEX]]"]
mode: meta
reviewed_by: codex
tags: [type/constitution, status/active, polaris, governance]
---

# Governance — 문서 성숙도 3단계

> 모태 governance.md 패턴 ported. Codex 디베이트 합의 — P4 Validation Boundary에 편입.

## 3 단계 성숙도

| 단계 | 의미 | Lint 처리 | 신뢰도 |
|---|---|---|---|
| **DRAFT** | 작성 중, 미검증 | warn 허용 | 낮음 |
| **VERIFIED** | self-verify 통과 + 1개 외부 검토 (codex 또는 Jin) | 정상 | 중간 |
| **AUTHORITATIVE** | codex-debate 합의 + Jin ack 또는 다중 INSIGHT 누적 증거 | strict lint | 높음 |

## frontmatter 필드

```yaml
maturity: draft|verified|authoritative
verified_by: <agent or jin>
verified_at: YYYY-MM-DD
authoritative_basis: <ADR-NNN or INSIGHT 누적 N개>
```

## 단계별 사용 규칙

### DRAFT
- 새 INSIGHT/ADR/lesson 초기 작성
- 후속 작업의 의사결정 근거로 사용 금지 (warning만)
- 만료: 14일 내 verified로 격상 또는 폐기

### VERIFIED
- self-verify (verification-before-completion 스킬) + 1 외부 검토 통과
- 후속 작업 근거로 사용 가능 (단 codex/Jin 비판 시 즉시 재검토)
- 격상 트리거: codex-debate 통과 또는 Jin ack

### AUTHORITATIVE
- codex-debate 합의 + Jin ack 또는 INSIGHT 누적 증거 ≥ 3
- Constitution 또는 applied ADR
- 후속 작업의 강제 근거 (위반 시 lint fail)
- 격하 트리거: superseded 또는 expired

## 모드별 적용

| 모드 | 작업 산출물 기본 maturity |
|---|---|
| DEV | component note: verified (codex 리뷰 통과 시) |
| ALPHA | hypothesis: draft → verified (BACKTEST 통과) → authoritative (ADR 승격 시) |
| FORENSIC | INSIGHT: draft (즉시 작성) → verified (24h 내 self-verify) |
| DEBATE | ADR: provisional → authoritative (Jin ack) |

## 리뷰 권한

- DRAFT → VERIFIED: code-implementer (코드) / vault-curator (노트) / forensic-investigator (INSIGHT)
- VERIFIED → AUTHORITATIVE: codex-debate-partner (debate 합의) + Jin (ack)

## Lint 강제
- AUTHORITATIVE 노트의 `authoritative_basis` 누락 fail
- DRAFT 14일 초과 warn
- 후속 작업이 DRAFT를 강제 근거로 인용 시 warn
