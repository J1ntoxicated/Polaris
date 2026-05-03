---
entity_type: index
entity_id: patterns_readme
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INDEX]]"]
mode: meta
reviewed_by: jin
tags: [meta, patterns, polaris]
---

# Patterns — 코드/설계 패턴 + anti-pattern 카탈로그

## 목적

Polaris에서 반복적으로 사용/금지하는 패턴을 카탈로그화. 새 컴포넌트 작성 시 참조.

## 카테고리

### 1. Pure Core 패턴 (P6)
- `parse_message()` 패턴 (모태 ws_feed_spot 참조) — pure parser, no I/O
- 결정론적 sizing 함수
- 결정론적 regime 분류

### 2. Imperative Shell 패턴 (P6)
- WS reconnect with exponential backoff
- DB writer 단일화
- REST retry with circuit breaker

### 3. Property-based test 패턴 (P7)
- numeric column NULL 처리 검증
- 경계값 (0, max, NaN, Inf)
- monotonicity invariant (예: cell score monotone in input)

### 4. Anti-patterns (금지)
- ❌ vault에서 machine state 직접 write (P1 위반)
- ❌ ADR proposed 7일 초과 방치 (P2 위반)
- ❌ 본인 코드 본인 리뷰 (ADR-004 위반)
- ❌ 단일 파일 1,000+ 라인 (단일 책임 위반)
- ❌ TODO/FIXME 0건이지만 잔재 가득 (모태 spot 함정)

## 작성 규칙

신규 패턴 발견 시 (예: codex 리뷰에서 반복 등장):
1. `vault/30_knowledge/patterns/PATTERN-NNN-<name>.md` 작성
2. 백링크 ≥ 2 (관련 INSIGHT/component)
3. anti-pattern은 lint 자동 강제 가능 시 hook에 편입
