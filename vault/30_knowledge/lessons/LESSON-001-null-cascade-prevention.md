---
entity_type: lesson
entity_id: LESSON-001
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles]]", "[[INSIGHT-006]]", "[[INSIGHT-010]]"]
mode: meta
reviewed_by: codex
maturity: authoritative
authoritative_basis: 모태 lessons #78 (629 NULL row → 3 downstream crash)
tags: [type/lesson, status/active, scope/spot, polaris]
---

# LESSON-001 — NULL Cascade Prevention (모태 lessons #78 인수)

## Trigger (모태 사건)

Harness가 `trades.status='open'` 585건 중 508건을 `UNKNOWN_BACKFILL` 처리하면서 `pnl_pct=NULL` → orphan 122 합쳐 **629 NULL row** → 다음 3 downstream 연쇄 crash:
1. `strategy/backtester.py tier1_replay` — `pnl -= _slip_bps/100` (None -= float) → evolver dead
2. `dashboard/operations.py:222` — `f'${_lp:+.1f}'` None format → dashboard crash
3. `position.py:195 from_dict` — `d.get("pnl_pct", 0)` 가 key+NULL 시 None → ai_controller 잠재 crash

## Rule

**numeric column에 NULL 입력 절대 금지. NULL 가능 column은 boundary에서 명시 coerce.**

## Why

NULL은 cascade 효과 — 한 column NULL이면 downstream computation이 모두 None/Error. 모태 629 NULL row가 3 곳 동시 crash의 직접 증거.

## How to Apply (Polaris)

### DB Schema (Phase 2b config)
- numeric column NOT NULL DEFAULT 0
- 예: `pnl_pct REAL NOT NULL DEFAULT 0`, `fee_paid_usdt REAL NOT NULL DEFAULT 0`

### Code (P6 + P7)
- numeric value read 시: `value or 0` (단 0이 의미있는 값일 때 주의)
- Optional[float] type 명시 (mypy/pyright strict)
- DB read boundary: Position.from_dict, store.load_trades, dashboard.load_trades 등 모두 None coerce

### Test (P7)
- Hypothesis property-based test: numeric field에 None / NaN / Inf 입력 → 함수 stable 검증
- DB integration test: NULL row insert 후 downstream 함수 작동 확인

## Lint Enforcement

vault_lint는 코드 패턴 직접 검사 X — code-implementer가 신규 numeric 함수 작성 시 NULL handling test 의무 (40_components 노트 + property-based test 적용).

## Related
- INSIGHT-006 (frozen params boundary)
- INSIGHT-010 (fee_paid base units bug — 같은 cascade)
- principles P7 (Property-based testing 우선)
