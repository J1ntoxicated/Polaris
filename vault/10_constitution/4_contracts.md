---
entity_type: constitution
entity_id: 4_contracts
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles]]", "[[north_star]]", "[[INDEX]]"]
mode: meta
reviewed_by: codex
tags: [type/constitution, status/active, polaris, contracts]
---

# 4 Contracts — 상태 관리 시스템 계약

> Codex 디베이트 1 라운드 결정적 통찰 — M1~M4(SSOT 다중화/결정 반감기/유기적 연결/메타 작업)는 1개 상위 실패의 4 표상. 그 상위 실패 = "권위/수명/쓰기 권한/검증 경계가 정의되지 않은 상태 관리 시스템". → 4 contract 명시화로 해결.

## Contract A — Authority (권위)

**무엇이 진실원천인가.**

| 영역 | SSOT |
|---|---|
| Machine state (price, position, order, fill, learning value) | Code / DB |
| Human knowledge (사고/결정/spec/lessons/패턴) | Vault |
| Derived view (machine → human readable) | `vault/generated/` (gitignore, untracked) |

### 위반 검출
- vault md 안에 machine state write 흔적 → vault_lint fail
- DB row를 vault에서 직접 수정 → 금지
- 같은 사실 2곳에 다른 값 → contradiction lint fail

---

## Contract B — Lifecycle (수명)

**모든 사실/결정의 수명과 만료 규칙.**

| 노트 type | 기본 만료 | 만료 트리거 |
|---|---|---|
| ADR | applied 후 superseded 또는 명시 expired | proposed 7일 초과 → 폐기 또는 ack 강제 |
| INSIGHT | 6개월 미참조 | 관련 ADR applied +30일 또는 superseded |
| HYPOTHESIS | 결과 도달 +30일 | graduated → ADR / archived |
| Lesson | never | superseded 시만 |
| Component note | never | 코드 폐기 시 archived |

### Lint 강제
- `expires` 누락 fail
- 만료된 노트 active 상태 fail
- proposed 7일 초과 warn

---

## Contract C — Write Path (쓰기 권한)

**누가 어디에 쓸 권한.**

| 영역 | 쓰기 권한 |
|---|---|
| Constitution | **Jin only** |
| ADR (provisional) | vault-curator (codex-debate 통과 후) |
| ADR (applied) | Jin (ack 후 자동) |
| INSIGHT | vault-curator (forensic-investigator 산출 의무 1개) |
| Lesson | vault-curator |
| Component note | code-implementer (40_components/), curator review |
| Runtime log | harness (append-only) — **paper runner 도 50_runtime/paper_log_*.md append 권한 (alpha 검증 사이클 일부, ADR-010)** |
| Hypothesis | Jin + vault-curator |
| Code | code-implementer + Jin |
| Test | code-implementer (TDD 의무) |
| DB | 봇 코드 (writer 단일화 — race 차단) |

### Hook 강제
`pre_agent.py`가 agent invoke 전에 위 매트릭스 위반 검출 시 차단.

---

## Contract D — Validation Boundary (검증 경계)

**어디서 검증되어야 다음 단계로 갈 수 있는가.**

### 코드 boundary
```
write code
  → unit test 통과 (TDD)
  → property-based test 추가 (P7 적용 영역)
  → 40_components/<name>.md 갱신 (curated)
  → vault_lint 통과 (orphan/expires/reviewed-by/machine-state-leak)
  → codex 외부 리뷰 (max 3 라운드 합의)
  → commit (pre_commit hook 재검증)
```

### 알파 boundary
```
HYPOTHESIS-NNN
  → fast-fail gate: 수학적 생존 가능성 (BACKTEST 24h 내)
  → BACKTEST: 정량 임계값 통과
  → PAPER: 최소 N trades or X일 + slippage gap 허용 범위
  → Promotion Gate: paper/live diff + sizing cap + kill criteria + rollback plan
  → ADR 승격 (라이브 결정) 또는 archived (실패)
```

### 모드 boundary
한 작업 = 한 모드. 모드 전환 시 explicit transition (이전 모드 산출물 closing → 새 모드 진입).

### 긴급 boundary 우회
[[emergency_bypass]] — bypass 후 24h 내 사후 산출물 (provisional ADR + component note + lessons 신규) 의무.

---

## 4 Contract 통합 검증

매 commit 전 4 contract 모두 통과해야 함:
- A 위반 (machine state in vault) → fail
- B 위반 (expires 누락 / proposed 7일 초과) → fail/warn
- C 위반 (쓰기 권한 위반) → pre_agent hook 차단
- D 위반 (검증 단계 누락 / codex 미리뷰) → pre_commit hook 차단

긴급 bypass = `EMERGENCY=1` env 설정 + 24h 사후 추적 (Constitution emergency_bypass 참조).
