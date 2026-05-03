# Canonical Cell Matrix — Decision SSOT

Plan: [`cell-matrix-100pct-pivot.md`](../plans/cell-matrix-100pct-pivot.md) (5 phase, 30-40h)

## SSOT Role

`strategy_cell_matrix` 가 모든 trade decision 의 SSOT:
- Sizing (`cell_score_mult`)
- Exit threshold (`optimal_trail / bep / max_hold / hard_stop`)
- Direction (`cell_score_long` vs `cell_score_short`)
- Provider weight (Phase 4, `cell × provider` matrix)
- Strategy Elo (Phase 5, `(cell_key, strategy) → Elo`)

## 8-dim cell key

`(exchange × group × session × regime × strategy × direction × ticker × liquidity_tier)`

## 파일 / 테이블

| Item | Path / Table |
|------|--------------|
| Code | `invasion/strategy/cell_matrix.py` |
| Main table | `strategy_cell_matrix` |
| Cell Provider Weight (Phase 4, planned) | `cell_provider_weight` — `(cell_key, provider, weight, n_samples, last_updated)` |
| Cell × Strategy Elo (Phase 5, planned) | `(cell_key, strategy) → Elo` in evolver.py |

## Phase 별 schema 확장

| Phase | column / table 추가 |
|-------|---------------------|
| Phase 1 | (기존) `cell_score_mult` 단일화 — ticker/session/tier mult 흡수 |
| Phase 2 | `optimal_trail_activate REAL`, `optimal_bep_activate REAL`, `optimal_max_hold_sec INTEGER`, `optimal_hard_stop_pct REAL` |
| Phase 3 | `cell_score_long REAL`, `cell_score_short REAL` |
| Phase 4 | `cell_provider_weight` 신규 테이블 |
| Phase 5 | evolver Elo 를 per-cell 로 분리 |

## preg ↔ cell 관계

- `preg` = global default fallback (cell sample 부족 시)
- `cell` = per-cell learned override (hourly_stats learner 업데이트)
- FROZEN (clean_data_epoch, kill_switch, safety) 만 hardcode 허용

## 참조
- [canonical_files.md](canonical_files.md)
- [coding_conventions.md](coding_conventions.md) — Cell-aware Decision Pattern
- [audit_framework.md](audit_framework.md) — Cell SSOT audit
