---
entity_type: constitution
entity_id: north_star
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INDEX]]", "[[principles]]", "[[ADR-001]]"]
mode: meta
reviewed_by: jin
tags: [type/constitution, status/active, scope/spot, polaris, north_star]
---

# 북극성 (North Star) — Polaris

> Polaris의 영속 철학. Jin only edit. 변경 시 ADR 필수.

## 1. Mission

**SPOT crypto + 주식 거래에서 알파를 검증·진화시키는 시스템을 만든다.**

"더 좋은 봇"이 아니라 **"수익이 안 날 때 즉시 추적 가능 + 컨텍스트 폴루션이 구조적으로 불가능한 봇"**.

## 2. 철학 (모태 보존 + SPOT-first 재정의)

### 2.1 모태 보존

> **"어느 시장 상황에서도 수익이 있다"** — Aggressive Contrarian.

- All regimes ATTACK, no defense
- Crisis = opportunity, max bet on fear
- 위기 = 진입 기회 (역발상 우선)

### 2.2 SPOT-first 재정의 (Polaris)

모태는 멀티 거래소(crypto perp, US stocks, forex CFD)에서 SPOT으로 pivot한 결과 누더기. Polaris는 처음부터 SPOT 도메인 first-principles로 시작:

- **자산 단순화**: 처음엔 OKX SPOT crypto + (선택) Alpaca US stocks paper. perp/CFD/forex 영역 진입 금지 (ADR 통한 명시 결정 필요).
- **Fee-aware**: SPOT은 fee가 클 수 있음 (모태 INSIGHT-032가 "OKX SPOT scalp 수학적 불가능" 발견). 모든 알파는 fee × 2 < expected_TP 통과해야 함 (fast-fail gate).
- **Settlement-aware**: SPOT은 즉시 settlement. 페이퍼/라이브 격차가 perp보다 작음 (모태 lessons #47 paper/live 격차).

## 3. 영속 원칙 요약 (자세히는 [[principles]])

P1. Authority 분리 (Code/DB=machine SSOT, Vault=human knowledge hub)
P2. Lifecycle 강제 (모든 결정에 expires)
P3. Write Path + Provisional ADR (Constitution=Jin only)
P4. Validation Boundary (코드 + 알파 양쪽)
P5. Alpha-first KPI (MTTR-alpha 주, vault 품질은 derived)
P6. Pure Core + Imperative Shell
P7. Property-based Testing 우선

## 4. 절대 금지 (모태 실패에서 학습)

- ❌ 컨텍스트 폴루션 — 같은 사실이 5곳에 산재 (M1 SSOT 다중화 / lessons #80)
- ❌ 결정 미적용 방치 (ADR proposed 7일 초과)
- ❌ 단일 agent 작성+리뷰 (코드 리뷰는 codex 외부 의무)
- ❌ 자율 forensic loop (메타 작업 무한 증식)
- ❌ machine state를 vault에 직접 작성 (P1 위반)
- ❌ vault orphan 노트 (M3 유기적 연결 부재)
- ❌ "더 좋은 더 많은" 추가 (단순화가 우선)

## 5. 성공 정의

- 첫 알파 가설이 BACKTEST → PAPER → ADR 사이클 완주 (Phase 2)
- 첫 컴포넌트가 codex 리뷰 통과 + property-based test 적용 (Phase 2)
- vault_lint 항상 0 violation
- MTTR-alpha 측정 가능 상태 (Phase 4)
- 6개월 후 vault 노트 ≤ 200 + ADR ≤ 30 + INSIGHT ≤ 50 (메타 작업 한도)
