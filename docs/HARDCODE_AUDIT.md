# HARDCODE AUDIT — 2026-04-17

**Commissioned**: Jin 02:09 "하드코딩 전수조사해서 바꿀수있는건 자율 학습으로"
**Executed**: Dev (MSG-HARDCODE-AUDIT, 🟡 P1)
**Scope**: `param_registry.py`, `regime_presets.json`, `trade/exit.py`, `signals/*`

---

## Headline

| 지표 | 값 |
|---|---|
| 총 등록 param (`_reg` entries) | **299** |
| 실제 adaptive (pset 호출됨) | **2** (ticker_direction_bias, whitelist_size_boost) |
| Static seed-only | **297** |
| 자율 커버리지 | **0.67%** |

Jin 진화 모델 기대치와 실상의 괴리 — AI/learner 구조는 존재하나 대부분 seed 에 고정.

---

## Tier 분류

### Tier 1 — 북극성 직결 (41개)
`min_score`, `direction_weight_*`, `atr_mult_*`, `hard_stop_*`, `time_exit_max_*`, `slippage_size_adjust_*`, `neutral_timeout_*`, `score_inversion_*`, `flat_auto_block_*` 등.

수익 직결 — empirical 성과 ↔ param 감도 가장 높음.

### Tier 2 — 공격량 튜닝 (47개)
`exit_{vol,hold,trail}_mult_{group}`, `cooldown_{group}`, `position_size_mult_{session}`, `decay_*`, `early_flat_*`, `flat_kill_mult_*`, `trail_*`.

Group/session 별 정량 튜닝 영역.

### Tier 3 — 운영/구조 (211개)
그 외 — fee rate, API rate limit, dashboard param, schema 등. 대부분 adaptive 비대상.

---

## Top 20 전환 후보 (Tier 1/2 교차 empirical)

| # | Param | Seed | 조정 주체 | 근거 |
|---|---|---|---|---|
| 1 | `min_score` | 50 | evolver + adaptive_tuner | 72h low-score WR 47.9% > high-score 37% (score_inversion 기반) |
| 2 | `direction_weight_stock_short` | 1.0 | adaptive_tuner (hourly) | Ops 실측 stock short bleed (+$813 long vs short loss) |
| 3 | `direction_weight_crypto_short` | 1.0 | adaptive_tuner | OKX short 집중 손실 패턴 (MSG-ATTACK-REDESIGN #1) |
| 4 | `neutral_weak_threshold` | 10 | adaptive_tuner | neutral regime 72.8% empirical 의존 재조정 |
| 5 | `atr_mult_crypto/stock/etf` | 1.0/0.7/0.7 | adaptive_tuner | 그룹별 volatility 실측 연동 |
| 6 | `time_exit_max_negative_pct` | -2.0 | adaptive_tuner (24h) | TIME negative bucket avg 재측정 (현재 -23%) |
| 7 | `neutral_timeout_sec` | 1800 | adaptive_tuner | dormant 87% 경험 기반 30min 재검증 |
| 8 | `neutral_timeout_max_peak` | 0.5 | adaptive_tuner | winner 커버리지 vs zombie 균형 |
| 9 | `slippage_size_adjust_threshold_pct` | 30.0 | adaptive_tuner | slippage_tracker 자료 누적 후 per-group 자동 |
| 10 | `slippage_size_adjust_mult` | 0.5 | adaptive_tuner | 효과 측정 후 최적화 |
| 11 | `score_inversion_threshold` | 40.0 | adaptive_tuner | 72h bucket-wise WR curve 기반 |
| 12 | `score_inversion_factor` | 0.01 | adaptive_tuner | bucket 기울기 경사 자동 |
| 13 | `flat_auto_block_sec` | 3600 | adaptive_tuner | ticker 회복 시간 distribution 기반 |
| 14 | `flat_auto_block_peak_pct` | 0.2 | adaptive_tuner | FLAT bucket 정의 tune |
| 15 | `exit_hold_mult_stock` | 8.0 | ticker_learner (per-ticker) | group 전체 아니라 ticker 별 |
| 16 | `exit_trail_mult_crypto` | 1.0 | adaptive_tuner | crypto 변동성 경사 |
| 17 | `early_flat_sec` | ? | adaptive_tuner | early_flat WR 기반 |
| 18 | `flat_kill_mult_mid` / `_high` | ? | adaptive_tuner | 구간 WR 경사 기반 |
| 19 | `position_size_mult_us` | 1.2 | adaptive_tuner (session-aware) | US 세션 실측 재조정 |
| 20 | `cooldown_crypto` | 90 | adaptive_tuner | ticker 재진입 gap empirical |

---

## Roadmap — 3-phase (Jin 원칙 준수)

### Phase 1 (현재 sprint) — Structure audit + 2-3 keys adaptive
- 본 audit 문서 (이 파일) = 1차 증거 자료
- `direction_weight_*` 전 세트 (16개) adaptive_tuner 연동
  - 1h cycle: per-group-direction trade WR 측정 → 하향 (WR<40%) 또는 상향 (WR>55%)
  - Jin 원칙 "관대한 default + 점진" → max step 0.05 per cycle, bounds (0.3, 2.0)
- 신규 구조 X — adaptive_tuner.py 기존 pset 경로 확장

### Phase 2 (empirical 안정 후) — Tier 1 전체 adaptive
- `min_score`, `neutral_weak_threshold`, `time_exit_max_negative_pct`, `neutral_timeout_*`
- 매 24h cycle, per-regime 세분화
- `param_history.jsonl` 누적 + rollback 가능성 확보

### Phase 3 (장기) — Tier 2 group-aware learner
- `exit_*_mult_{group}` group별 ticker_learner 확장
- evolver 가 fitness 기반 seed 자체 변이 (현재는 strategy JSON 만 변이)

---

## Jin 원칙 검증

1. **하드코딩 금지 방향** — 현재 0.67% adaptive 는 사실상 하드코딩. Phase 1-3 로 단계 증가.
2. **AI/자율 학습** — adaptive_tuner (시간 기반) + ticker_learner (성과 기반) + evolver (유전적) 3-tier 이미 구조 존재, 적용 범위만 확장.
3. **북극성 공격 보존** — 모든 adaptive 는 **공격량 증대 방향** default (WR>55% 시 size 증가, WR<40% 시 감소). tight/디펜시브 tune 은 명시적 Ops greenlight 필요.
4. **점진적 전환** — Phase 1 에 16 param (direction_weight) 만, 결과 empirical 확증 후 확대.
5. **코드 꼬임 방지** — 기존 adaptive_tuner.py / ticker_learner.py / evolver.py 경로 재활용. 신규 learner 모듈 금지.

---

## 다음 Dev action

Phase 1 실행 = `adaptive_tuner.py` 에 `_tune_direction_weights()` 함수 추가 + hourly cron hook. 별도 Dev sprint 로 분리 (본 audit 완료 후 Harness 판단).
