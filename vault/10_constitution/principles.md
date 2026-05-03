---
entity_type: constitution
entity_id: principles
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[north_star]]", "[[4_contracts]]", "[[INDEX]]"]
mode: meta
reviewed_by: codex
tags: [type/constitution, status/active, polaris, principles]
---

# 7 영속 원칙 (P1 ~ P7)

> Codex 디베이트 3 라운드 합의 (95→100%). Jin only edit. 변경 시 ADR 필수.

## P1 — Authority 분리

**Code/DB = machine state SSOT** (실시간 상태/가격/포지션/주문/학습값)
**Vault = human knowledge hub** (사고/결정/spec/lessons/components 매핑)

### 명문 (lessons #80 인용)

> **"Vault는 machine state를 생성하지 않는다. machine state를 설명하거나 참조만 한다."**

### 위반 예시 (금지)
- vault 노트에서 `portfolio_state.json` 직접 편집해서 봇이 reload
- vault 노트 안에 DB INSERT/UPDATE 명령 넣어서 실행

### 허용 예시
- vault 노트에 "현재 포지션 요약" dataview query (read DB → render md)
- vault에 derived view (`vault/generated/components/`, gitignore)

### Lint 강제
`tools/vault_lint.py`가 vault md 안에 machine state write 흔적 발견 시 fail.

---

## P2 — Lifecycle (모든 결정에 만료)

**ADR/INSIGHT/HYPOTHESIS는 `expires` frontmatter 필수.**

### 규칙
- ADR 기본 만료: applied 후 superseded 또는 명시 expired
- ADR `proposed/provisional` 상태: max 7일 → 자동 폐기 또는 Jin ack 강제
- INSIGHT 만료 트리거: 관련 ADR 적용 +30일 OR 명시 superseded OR 6개월 미참조
- 만료 시 자동 `#status/expired` 태그 → dataview 쿼리에서 제외

### Lint 강제
- `expires` 누락 = fail
- `proposed` 7일 초과 = warn + Jin escalation

---

## P3 — Write Path + Provisional ADR

### 편집 권한
| 영역 | 권한 |
|---|---|
| Constitution (10_constitution/) | **Jin only** (영속 원칙, 위임 불가) |
| ADR (20_decisions/) | Jin + vault-curator (provisional 상태) |
| INSIGHT/lesson (30_knowledge/) | vault-curator (백링크 ≥ 2 강제) |
| Component note (40_components/) | code-implementer (curated only) |
| Runtime log (50_runtime/) | harness (append-only) |
| Hypothesis (60_alpha/) | Jin + vault-curator |
| Code (Polaris/invasion 또는 src) | code-implementer + Jin |
| DB | 봇 코드 (writer 단일화) |

### Provisional ADR 흐름 (Jin 단독 병목 완화)
1. codex-debate 통과 → ADR `provisional` 상태로 vault-curator가 작성
2. provisional ADR은 hook/lint 차단 근거 X (effect 없음)
3. Jin `ack` 시 frontmatter `ack_by: jin`, `ack_at: timestamp` → applied
4. Jin ack 안 하면 7일 후 자동 폐기 (P2 적용)

---

## P4 — Validation Boundary (코드 + 알파)

### 코드 boundary
```
변경 → unit test 통과 → 40_components 노트 update → property-based test (P7) 적용 → vault lint 통과 → codex 외부 리뷰 (max 3 라운드 합의) → commit (pre_commit hook 재검증)
```

### 알파 boundary
```
HYPOTHESIS → fast-fail gate (BACKTEST 24h 수학적 생존성) → BACKTEST → PAPER (최소 N trades or X일) → Promotion Gate (paper/live diff + sizing cap + kill criteria + rollback plan) → ADR 승격 (라이브)
```

### 실패 가설 처리
- 실패 시 ADR 승격하지 않음. INSIGHT/lesson으로 닫음 (decision layer 오염 방지).

### 긴급 fix 예외
[[emergency_bypass]] 참조.

---

## P5 — Alpha-first KPI

### 주 KPI: MTTR-alpha
**Mean Time To Recovery — alpha 성과 회복 시간.**
- 정의: 성과 이상 탐지(out of control band) 시점 → rolling Sharpe / hit rate / MDD / expectancy가 control band 내 N trade 또는 X일 연속 복귀까지 시간
- 측정 시작: Phase 4 (점진 확장 시점)

### 보조 KPI
- `drawdown half-life` (회복 절반 시간)
- `recovery area` (회복 전까지 기대수익 손실 면적)
- `diagnosis-to-patch` (이상 발견 → fix 코드 작성)
- `patch-to-stable` (fix → control band 안정)

### Vault 품질 = derived metric
- "vault orphan 0"은 KPI 아님
- vault 품질은 MTTR-alpha 단축 효과로만 측정
- 메타 작업이 거래 분석 압도하면 즉시 조정

---

## P6 — Pure Core + Imperative Shell

### 분류
- **Pure**: 알파/신호/scoring/regime/sizing 계산 — no I/O, deterministic
- **Shell**: I/O 래핑 (WS, DB, REST, file) — pure function 호출만

### 강제
- 신규 함수 작성 시 frontmatter `pure: true|false` 필수 (40_components 노트)
- pure function은 unit test + property-based test 의무
- shell function은 integration test 의무 + property-based test 권장

### 모태 모범 사례
`auto_invasion_mk1-main/invasion/spot/ws_feed_spot.py:parse_message()` — pure parser 패턴 (주석에 명시: "Parser is pure → unit-testable").

---

## P7 — Property-based Testing 우선

### 도구
**Hypothesis** 라이브러리 (Phase 1 하네스 도입과 함께).

### 적용 대상
- cell score 계산
- WS parser (모태 INSIGHT-035 fee 단위 버그 같은 경계값 잡기)
- regime 분류기
- sizing 함수
- 모든 numeric column NULL 처리 (모태 lessons #78 NULL cascade 차단)

### 효과
모태 반복 버그 70%+ (NULL cascade, 키 불일치, 필드 누락) 사전 차단.

### Lint 강제
40_components 노트에 `property_tests: <count>` 필드 권장. 0이면 warn.
