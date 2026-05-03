# Cell SSOT Audit

Plan: [`cell-matrix-100pct-pivot.md`](../plans/cell-matrix-100pct-pivot.md) — 100% 메트릭스화 감사 절차.

## 트리거

매 commit OR 새 mult layer 감지 OR 새 decision site PR.

## 실행 agent

`dev-audit-advisor` (발견) → `dev-refactor-advisor` (통합 spec) → `dev-coder` (구현).

## 검사 항목

1. **새 hardcode decision site** — `grep -nE "= [0-9]+\.?[0-9]*" invasion/**/*.py | grep -vE "(preg|cell_matrix|FROZEN)"` 결과 review. cell lookup 통합 가능성 평가.
2. **cell axis 중복 mult layer** — `ticker_mult` vs cell ticker axis, `session_mult` vs cell session axis, `tier_mult` vs cell liquidity_tier axis 등. 중복 감지 시 HIGH flag.
3. **mult chain depth** — `_pipeline_sizing.py` 의 multiplier 연쇄 깊이 측정. 목표 6 이내 (Phase 1 완료 후 3 목표).
4. **decision site pattern grep**:
   - `preg(` usage 에서 cell axis 적용 가능한 곳
   - `if score > 0: ... direction = "long"` hardcode direction
   - `* mult` 연쇄 chain
   - `composer.weight *` global weight
   → 모두 cell-aware 전환 후보

## 출력 포맷

```
### CELL-AUDIT-NNN
File: invasion/.../xxx.py:LLL
Kind: [HARDCODE / DUP_MULT / CHAIN_DEPTH / DECISION_SITE]
Evidence: <grep quote>
Cell axis 후보: <which axis absorbs>
Effort: <LOC>
Priority: [P0 / P1 / P2]
```

## 참조
- [audit_framework.md](audit_framework.md)
- [cell_aware_pattern.md](cell_aware_pattern.md)
- [canonical_cell_matrix.md](canonical_cell_matrix.md)
