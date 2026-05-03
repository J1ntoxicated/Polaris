# Polaris Structural Overhaul — Design Spec

**Date**: 2026-04-27
**Author**: Harness session (Jin mandate-driven)
**Status**: Draft → user review pending
**Skill**: superpowers:brainstorming → writing-plans (next)
**Vault refs**: [[INSIGHT-016]] [[INSIGHT-017]] [[INSIGHT-019]] [[INSIGHT-021]] [[ADR-003]] [[ADR-004]] [[canonical_cell_matrix]] [[feedback_no_block_filter_architecture]] [[feedback_no_defensive_param_dampen]] [[feedback_aggressive_always_profit]]

---

## 1. Context

### Trigger
24h trade audit (Th1-Th5 sequential-thinking) — NET **-$965/day** (24h closed 1842 trades). 손실 ~100% origin = TIME exit (993 trades / 26.8% WR / -$1436). Multi-exchange differential 진단 (Q2): OKX (mean-reversion 시장) 와 CAP (commodity = trend-following 시장) 가 본질적으로 다른 dynamics. Block paradigm (DEMOTE / quarantine / threshold gate) 모두 [[feedback_no_block_filter_architecture]] 위반 → 구조적 재설계 필요.

### Out-of-scope (별도 spec — Wave 3)
- Data layer (dual storage SSOT, sqlite race, jsonl bloat) — INSIGHT-015 / INSIGHT-006
- Observability layer — INSIGHT-002 / INSIGHT-008 silent modules
- Sentiment ingest hot fix — INSIGHT-018 (진단 log 이미 deploy, root cause 후속)
- Visualizer — INSIGHT-011 (UI 영역, trade economics 무관)

### North Star compliance (영속 원칙 적용)
- ✅ Block 누적 0 (DEMOTE 이미 폐기, 이 spec 도 block 추가 안 함)
- ✅ Amplify-only sizing (cell.mult 1.0~2.0, dampen X)
- ✅ Aggressive contrarian 유지 (winner extend, loser base — block X)
- ✅ Loss/profit asymmetry — TIME 0.39 ratio 정상화 방향 (cell-aware max_hold 로 winner 더 잡음)
- ✅ Paper account empirical 노출 허용 (DEMOTE 폐기 후 chronic loser 흐름 = 학습 데이터)

---

## 2. Section 1 — Cell-score-driven Signal Weighting

**Goal**: DEMOTE 폐기 후 chronic loser cell 자연 도태 메커니즘. Block/dampen 0건.

**Approach** (vault-grounded, amplify-only):
- **D1 (deferred to Wave 3)**: 신규 strategy class for trend market — signal layer redesign 광범위, engine.py `_remap_contrarian_score` 옆 `_remap_trend_score` 추가
- **D3 (Wave 2A)**: cell.mult 학습 ramp-up — winner sample threshold 50 → 20 으로 축소, 빠른 amplify

**Implementation (Wave 2A)**:
- `invasion/strategy/cell_matrix.py`: `_compute_cell_score` 의 sample threshold 학습 가속
- 기존 1.0~2.0 amplify-only range 유지
- 학습률 ramp_up_rate 상승 (preg key 추가)

**Vault evidence**:
- [[canonical_cell_matrix]] Phase 1 — `cell_score_mult` 단일화 완료 (이미 wire)
- [[INSIGHT-001]] BZ 9-fail — chronic ticker pattern, DEMOTE 가 막아왔으나 폐기 후 cell.mult 자연 학습으로 대체 필요
- _pipeline_sizing.py:230 — `_cell_mult` 이미 `_adaptive_mult` chain 에 multiply

---

## 3. Section 2 — TIME Redesign (Cell-aware Max_hold)

**Goal**: TIME 0.39 win/loss ratio root fix. amplify-only winner extend.

**Approach** (B-amplify, [[INSIGHT-016]] idea #5):
- `cell_matrix.optimal_max_hold_sec` 컬럼 학습 (canonical Phase 2 schema)
- `effective_max_hold = base + max(0, cell.score - 0.5) × extend_max` — winner cell only extend, loser base 유지
- [[INSIGHT-021]] hold-aware loser cut 와 결합 — 이미 deploy (commit `bc82d66e`), CAP 확장 (INSIGHT-024 forensic 후)

**Reject**:
- A (TIME 폐기 / TRAIL only) — STOP 0% WR -$5/trade × 7배 손실 risk

**Implementation (Wave 2A)**:
- DB schema: `ALTER TABLE strategy_cell_matrix ADD COLUMN optimal_max_hold_sec INTEGER`
- `cell_matrix.py`: cell aggregation 시 winner trade hold_seconds 분포 학습 (median or p75)
- `exit_cycle.py`: TIME 발생 시 cell.optimal_max_hold_sec 활용 (없으면 fallback group 고정)
- preg keys: `cell_max_hold_extend_max_factor` (default 2.0, range 1.0~3.0 amplify-only)

**Vault evidence**:
- [[INSIGHT-016]] idea #5 — "cell-level adaptive — `strategy_cell_matrix.optimal_max_hold_sec` 컬럼 활용" (P1)
- [[canonical_cell_matrix]] Phase 2 — `optimal_max_hold_sec INTEGER` 명시
- [[INSIGHT-021]] 이미 deploy — 1h+ OKX crypto -0.10% loss → fast cut

---

## 4. Section 3 — Asset-group Strategy Class

**Goal**: CAP commodity (trend market) fade-only signal mismatch 해소.

**D 검증 결과**:
- 현 시스템 contrarian 기반 (`engine.py:_remap_contrarian_score` core)
- Disabled trend strategies (`contrarian_commodity_g53/55/57`, `crypto_momentum_reversal_*`) 모두 trade_count=0 (evolver priority 못 받음)
- `MSG-P0-4-G11-KILL` 패턴 = Jin 직접 인정한 strategy retirement 메커니즘 (block 아님)

**Approach (B + D 결합)**:
- B: Disabled trend strategies 선별적 re-enable (DEMOTE 폐기됐으므로 chronic loss 차단 자체 사라짐, cell.mult 자연 학습)
- D: cell.mult 학습 ramp-up (Section 1 D3 와 통합)

**Reject**:
- A (signal layer trend logic 추가) — Wave 3 후속 (engine.py 광범위 redesign)
- C (manual family seed) — A 없으면 signal source 부족

**Implementation (Wave 2A)**:
- `contrarian_commodity_g53/55/57` (3) + `crypto_momentum_reversal_g1/g3/g4` (3) status flip: disabled → active (선별)
- `MSG-P0-4-G11-KILL` 영역 (`crypto_momentum_reversal_g11_ai × short`) 는 Jin 직접 retirement 이므로 유지
- evolver fitness ramp-up — initial fitness threshold 낮춤 (preg)

**Vault evidence**:
- [[INSIGHT-017]] direction-agnostic 보정 — long+short 모두 mismatch (commodity 전체)
- [[INSIGHT-024-cap-commodity-fitness-deficit-2026-04-27]] (작성 예정) — Wave 2A 동시
- `feedback_no_block_filter_architecture` — re-enable = amplify (block 아님)

---

## 5. Section 4 — Cell_matrix Phase 2-3 Schema

**Goal**: canonical_cell_matrix.md 의 Phase 2-3 schema 적용 (Section 2-3 dependency).

**Phase 2 schema (Wave 2A)**:
- `optimal_max_hold_sec INTEGER` (Section 2)
- `optimal_trail_activate REAL` (있으면 활용)
- `optimal_bep_activate REAL`
- `optimal_hard_stop_pct REAL`

**Phase 3 schema (Wave 2A 또는 후속)**:
- `cell_score_long REAL`
- `cell_score_short REAL`
- → direction-aware score (현재 single score 가 long/short 평균)

**Phase 4-5 (별도 spec, 후속)**:
- `cell_provider_weight` 신규 테이블 (Phase 4)
- evolver Elo per-cell (Phase 5)

**Implementation (Wave 2A)**:
- `invasion/data/store_core.py` 또는 schema 정의 파일에 ALTER TABLE migration
- `cell_matrix.py` aggregation logic 에 새 컬럼 학습
- backward-compat — 기존 컬럼 NULL 허용

---

## 6. Section 5 — ML_META + AI ExitAdviser Fallback

### ML_META filter
- 현재 SHADOW + 99.5% BLOCK verdict
- Decision: **SHADOW 영구 유지**. Activation = block paradigm (위반). ML 모델 자체 재훈련은 data layer 정합 후 (Wave 3 후속).
- 추가: SHADOW verdict 통계 monitoring 추가 (cron 30m) — 모델 fitness 평가 데이터 축적

### AI ExitAdviser silent fallback
- 실제 location: `invasion/ai_controller.py:_bg:460` — log "CTRL ExitAdviser failed, no detector.review_positions_fast fallback"
- 22:24 / 22:42 recurring — silent return path (no decisions)
- Decision: **Explicit fallback layer + log_event "warn"**
- Default fallback: cell.optimal_max_hold_sec / optimal_trail_activate 학습값 활용 (Phase 2 schema 의존)

**Implementation (Wave 2A)**:
- `invasion/ai_controller.py:_bg` (line 460 area) — fallback decision logic 추가
- 1차 fallback: `detector.review_positions_fast` (already attempted)
- 2차 fallback (신규): cell_matrix learned exit values per position (no manual rule)
- Log "warn" + counter (recurring 시 alert candidate)

**Vault evidence**:
- [[INSIGHT-008]] monitoring channel grep FP — silent module 검증 영역
- `feedback_audit_fstring_prefix_scan` — 영속 원칙 적용

---

## 7. Section 6 — ADR-003 Amplify-only Clamp 확장

**Goal**: [[ADR-003]] 의 3 surface preg `_mult` default<1.0 (winner-dampen) 위반 fix.

**Surface preg** (verified `_params_exit.py:342-346`):
| Preg | Current default | Current bound | Fix |
|---|---|---|---|
| `fsm_harvest_trail_mult_cap` | 0.5 | (0.1, 2.0) | default 1.0, bound (1.0, 2.0) |
| `fsm_harvest_trail_mult_alpaca` | 0.3 | (0.1, 2.0) | default 1.0, bound (1.0, 2.0) |
| `profit_cap_regime_mult_neutral` | 1.0 | (0.8, 1.5) | default 1.0 (OK), bound (1.0, 1.5) — lower 0.8 → 1.0 |

**Implementation (Wave 2A)**:
- `invasion/config/_params_exit.py`: 3 preg `_reg(...)` bound lower 1.0 으로 클램프
- default 값 1.0 으로 통일 (amplify-only mandate)
- ADR-003 status `proposed → applied`

**Vault evidence**:
- [[ADR-003]] — 3 surface preg semantic 분석 ready
- [[INSIGHT-004]] NEW-1 — `_mult` 39개 audit 후 surface

---

## 8. Implementation Batches (Wave 2A)

### Batch 1 — Schema + Foundation (1 commit)
- DB schema migration (`optimal_max_hold_sec`, `optimal_trail_activate`, `optimal_bep_activate`, `optimal_hard_stop_pct`, `cell_score_long`, `cell_score_short`)
- backward-compat NULL 허용

### Batch 2 — Cell.mult learning ramp-up (1 commit)
- `cell_matrix.py`: sample threshold 50→20, ramp-up rate up
- preg key `cell_score_ramp_up_rate` 추가

### Batch 3 — Cell-aware max_hold (1 commit)
- `cell_matrix.py`: `optimal_max_hold_sec` 학습 logic
- `exit_cycle.py`: TIME 분기 시 cell value 활용 + amplify-only winner extend

### Batch 4 — ADR-003 clamp fix (1 commit)
- `_params_exit.py`: 3 surface preg bound + default 1.0
- ADR-003 status update

### Batch 5 — Disabled trend re-enable + AI fallback log (1 commit)
- DB UPDATE strategies status (선별 6개, fitness DESC):
  - `crypto_momentum_reversal_g1_gauss` (33.0)
  - `contrarian_commodity_g55_gauss` (25.93)
  - `contrarian_commodity_g54_ai` (24.0)
  - `contrarian_commodity_g1_bayes` (23.98)
  - `contrarian_commodity_g53_ai` (23.95)
  - `contrarian_commodity_g57_bayes` (23.92)
- `MSG-P0-4-G11-KILL` 영역 (`crypto_momentum_reversal_g11_ai × short`) 유지 (Jin 직접 retirement)
- `ai_controller.py:_bg` line 460: 2차 fallback (cell.optimal_*) + log "warn"

**총 5 commits, 단계별 verification, 자율 restart**.

---

## 9. Verification Plan

### Per-batch
- AST + import smoke
- `python3 -m invasion --headless` boot test (각 batch 후)
- 30m runtime tick observation (1-2 cycle)

### End-to-end (5 batches 적용 후)
- 24h SQL audit 재실행:
  - TIME 26.8% → 35%+ WR target
  - 1-2h death zone -$854 → < -$200 target ([[INSIGHT-021]] + cell-aware max_hold 결합)
  - Commodity NET -$369 → break-even target (re-enable trend + cell.mult learning)
- cell_matrix snapshot 비교 (winner cell amplify 가속 입증)

---

## 10. Risk

- **Cell.mult ramp-up** 너무 빨라서 false-positive amplify (낮은 sample 로 winner 잘못 판단) — sample threshold 20 도 신중 모니터링
- **Disabled trend re-enable** — historical trade_count=0 (한 번도 fire X) 라 unknown unknowns
- **Phase 2-3 schema 추가** 후 기존 cell_matrix aggregation 호환성 — backward-compat NULL 처리 검증
- **Paper account 손실 노출** Wave 2A 적용 중 — Jin mandate 정합 (empirical 데이터)

---

## 11. Dependencies + Sequencing

- Wave 2A (이 spec) → Wave 2B (Section 1 D1 signal layer trend redesign — engine.py) — 별도 spec
- Wave 3 (data layer + observability) → 독립적, parallel 가능

---

## 12. References

- Vault: [[INSIGHT-016]] [[INSIGHT-017]] [[INSIGHT-019]] [[INSIGHT-021]] [[ADR-003]] [[ADR-004]] [[canonical_cell_matrix]] [[INSIGHT-001]] [[INSIGHT-024-cap-commodity-fitness-deficit-2026-04-27]] (작성 예정)
- Memory: [[feedback_no_block_filter_architecture]] [[feedback_no_defensive_param_dampen]] [[feedback_aggressive_always_profit]] [[feedback_loss_profit_asymmetry]] [[feedback_overhaul_over_incremental]] [[feedback_sequential_superpowers_vault_organic]]
- Code: `invasion/strategy/cell_matrix.py`, `invasion/trade/exit_cycle.py`, `invasion/trade/_pipeline_sizing.py`, `invasion/config/_params_exit.py`, `invasion/signals/engine.py`
