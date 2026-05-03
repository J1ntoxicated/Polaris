# Cell-aware Decision Pattern

Plan: [`cell-matrix-100pct-pivot.md`](../plans/cell-matrix-100pct-pivot.md)

## 원칙

새 decision logic 작성 시 cell lookup 우선 → preg fallback → FROZEN hardcode only.

## 우선순위

```
1. cell_matrix.lookup(cell_key, <column>)   # per-cell learned
2. preg(<param_name>)                        # global default
3. hardcode (Tier 1 FROZEN 만)               # safety invariants only
```

## 예시 (정/오)

```python
# ✅ CORRECT — cell lookup 우선
cell_val = cell_matrix.lookup(cell_key, "optimal_trail_activate")
trail = cell_val if cell_val is not None else preg("trail_activate")

# ❌ FORBIDDEN — hardcode mult/threshold (FROZEN 영역 외)
trail = 0.5

# ❌ FORBIDDEN — global-only (cell axis 무시)
trail = preg("trail_activate")  # cell 학습 우회

# ❌ FORBIDDEN — 덧대기 (새 mult layer 추가)
size = base * regime_mult * session_mult * ticker_mult  # cell axis 중복
# → cell_score_mult 단일화 (Phase 1)
```

## FROZEN 영역 (hardcode 허용)

- `clean_data_epoch` (1775839507)
- `kill_switch` 임계치
- WAL checkpoint 간격
- bounds min/max, schema enum 정의
- 그 외 **모든 숫자** 이관 의무

## Decision Category 별 cell column

| Decision | cell column | preg fallback |
|----------|-------------|----------------|
| Sizing | `cell_score_mult` | `size_base_mult` |
| Exit trail | `optimal_trail_activate` | `trail_activate` |
| Exit BEP | `optimal_bep_activate` | `bep_activate` |
| Exit time | `optimal_max_hold_sec` | `max_hold_sec_*` |
| Exit stop | `optimal_hard_stop_pct` | `hard_stop_pct_*` |
| Direction | `cell_score_long` vs `cell_score_short` | `score > 0` |
| Provider weight | `cell_provider_weight.weight` | `provider_weight_*` |
| Strategy Elo | `(cell_key, strategy) → Elo` | global Elo |

## 참조
- [coding_conventions.md](coding_conventions.md)
- [canonical_cell_matrix.md](canonical_cell_matrix.md)
