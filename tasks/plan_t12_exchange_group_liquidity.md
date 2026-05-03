# Plan T12+ — Exchange×Group×Liquidity 3-axis Tuning

> 수립: 2026-04-21 T12 / 토큰 고갈 전 아카이브. 다음 세션 fresh budget 으로 실행.

## 🚨 구현 규칙 (절대 원칙) — `feedback_no_hardcode_in_plans`

**Plan 내 모든 숫자는 "seed / 관측치 / 예시" — 구현 시 반드시 preg + learner 경유.**

- 본 Plan 에 기재된 임계값·배율·시간·윈도우·exponent 등 모든 상수:
  - `(seed)` = 초기값으로 register, 이후 learner 가 자동 튠
  - `(observed)` = 측정된 baseline, 목표/참조용만 (설계 상수 아님)
  - `(example)` = 독자 이해용 예시, 구현에 그대로 쓰면 위반
- 구현 시 체크:
  - 매직넘버 0건
  - 모든 threshold = `preg_*` key 로 등록 (bounds + seed + learner scope)
  - 공식 내 상수(지수/offset/divisor) 도 preg (예: `age_bonus_exp`, `ret_cap`)
  - 시간 윈도우 / 연속 카운트도 preg (`sustained_loss_window_hours` 등)
- 예외: SQL 컬럼명, protocol version, enum 값 (trading behavior 무영향)
- 위반 시 Plan/구현 즉시 거부 + feedback_no_hardcode_in_plans 재읽기

## 북극성 정합
- Scale-independent (normalize API 통일)
- Data-driven (모든 threshold preg + learner)
- Realtime (tick-level recalc)
- Amplify-only (mult ≥ 1.0 — 이것만 구조적 하한, 북극성 invariant)

## 설계 (Universal 3-layer 확장)

| Layer | 현재 | 제안 |
|---|---|---|
| L1 base | `{group}` | **`{exchange}_{group}`** composite |
| L2 ticker | ATR + ADX | ATR + ADX + **liquidity** 🆕 |
| L3 regime | regime_mult | 변경 없음 |

## 재료 현황 (이미 존재, 연결만 필요)

- ✅ 분봉 저장: `data/candles/*_MINUTE_5.json`, `_MINUTE_15.json` (286MB)
- ✅ `ticker_baseline` 테이블 + `normalize()` API (5-metric: atr/size/signal/volume/pnl_std)
- ✅ `ticker_dynamics` 테이블
- ✅ 6 hourly learner 체계 (max_hold/profit_cap/trail_mult/bep_activate/regime/session)
- ❌ 분봉 → ticker_baseline liquidity 갱신 loop **MISSING**
- ❌ Learner SQL 의 (exchange × group) composite aggregation **MISSING**
- ❌ Exit/entry 읽기 시 exchange-specific fallback chain **MISSING**

## Phase 1 — Exchange × Group composite (Core)

**대상 key 마이그**:
- `time_exit_max_age_sec_{group}` → `_{exchange}_{group}`
- `profit_cap_{group}` → `_{exchange}_{group}`
- `fsm_harvest_trail_mult` (global) → `_{exchange}_{group}` or `_{exchange}`
- `bep_activate` (global) → `_{exchange}_{group}` or `_{exchange}`
- `regime_size_mult_{regime}` → `_{exchange}_{regime}`

**Fallback chain** (backward compat):
```python
_preg(f"{k}_{exchange}_{group}") or _preg(f"{k}_{group}") or _preg(k)
```

**Learner SQL 변경**:
```sql
WHERE asset_group=? AND exchange=?  -- 각 (ex, grp) 루프
```

**검증 방법**:
- CAP forex vs (미래) OKX forex max_hold 독립 튜닝 확인
- CAP commodity vs CAP indices 분기 확인 (DB: exchange='cap' AND asset_group='indices')
- 기존 group-only key 유지 시 정상 fallback

## Phase 2 — Liquidity layer (L2 확장)

**분봉 → liquidity_score job** (hourly):
| Metric | 계산 |
|---|---|
| ADV | `sum(volume_5m) / N` (24h window) |
| spread_proxy | `mean((high-low)/close)` |
| body_ratio | `mean(|close-open|/(high-low))` |
| vol_spike | `volume_last_5m / volume_avg_30m` |
| realized_vol | `stdev(log(close/prev))` |

**저장**:
- `ticker_baseline` 에 `liquidity_adv / spread_bps / realized_vol` column 추가
- 기존 `normalize(ticker, metric, raw)` 에 `metric='liquidity'` 지원

**사용 (exit recalc Layer 2)**:
```python
liq_mult = normalize_liquidity(ticker)  # 0.5~2.0 multiplier
# micro-cap: liq_mult > 1.5 (trail wide, max_hold 연장)
# deep liquid: liq_mult < 0.8 (trail tight)
final_trail = base * ticker_tech * liq_mult * regime_mult
```

**검증 방법**:
- Micro-cap 티커 (BAT, CRV 등) vs deep liquid (BTC, ETH) 의 L2 mult 분리 확인
- CAP forex (상대적으로 낮은 분당 tick) vs OKX crypto (high tick density) 구분
- Spread 확대 이벤트 시 즉시 size 축소 검증

## Phase 3 — 지속 관찰 + 검증

**KPI (T12 baseline vs post-deploy)**:
| 지표 | T12 baseline | 목표 |
|---|---|---|
| 북극성 asymmetry | winner +0.32% / loser -0.33% (대칭) | winner ≥ 2× |loser| |
| CAP forex WR | 13% | ≥ 30% |
| BEP→loser 전환율 | 84/24h (peak 0.34 → -0.22) | ≤ 20/24h |
| TRAIL retention | 42% (peak 반납 58%) | ≥ 70% |
| TIME 비율 | cap 56% (58/104) | ≤ 30% |

**Gate**: Phase 1 완료 → 24h 관측 → asymmetry 개선 확인 후 Phase 2.

## Phase 1.3 — Signal Pipeline Hygiene (T12 신규)

**발견된 이상 (2026-04-22 00:00 조사)**:
1. `signals` 테이블에 **quality_gate 거부 기록** 혼입 → score 평균 -19.8 편향 (CAP)
2. 동일 ticker+direction 이 **1h 내 4번 반복** score (dedup/rate-limit 누락)
3. Provider signal scale 이 exchange 간 정규화 안 됨

**조치**:
- 통계 쿼리 `AND reason NOT LIKE 'quality_gate:%'` 기본 필터 추가
- 또는 `signal_blocks` 테이블 분리 (거부 vs emit 구분)
- Signal dedup: `(ticker, direction, provider_set)` 5min 내 중복 무시
- Signal normalize per (exchange, provider) — score scale 통일

**Wire 경로**:
- `invasion/signals/emit.py` — 기록 전 dedup check
- `invasion/ops/stats.py` — 거부 기록 별도 집계
- `normalize(ticker, 'signal_score', raw)` 확장

---

## Phase 1.4 — Data Hygiene / Noise Quarantine (T12 부분 적용)

**이미 적용 (2026-04-22 00:10)**:
- 75건 soft-quarantine (`status='quarantined_noise'`):
  - TEST% 티커 (1건)
  - ABS(pnl_pct)>50% (3건, price feed anomaly)
  - hold<5s + pnl=0 (71건, open/close glitch)
- **효과**: KPI loser -0.339 → -0.292 (0.047% 축소)

**다음 세션 추가**:
- Learner SQL 에 `strategy_id IS NOT NULL AND strategy_id!=''` 필터 추가 (241건 null_strategy 제외)
- pre_clean_epoch filter 일관 적용 (learner 마다 누락 확인)
- 실시간 anomaly guard: pnl_pct > 20% or < -20% 발생 시 즉시 `quarantined_anomaly` 태그
- Corporate action (split/spinoff) 자동 감지 → 해당 기간 ticker trade 제외

---

## 우선순위 (다음 세션, 갱신)

> **🚨 T13 첫 작업: `tasks/prep_t13_hardcode_audit_and_integration.md` 먼저 실행.**
> - Part A 하드코딩 전수조사 → `audit_t13_hardcoded.md` 생성
> - Part B 신/구 구조 매핑 → 시너지/중복 확정
> - Part C 체크리스트 기반 `plan_t13_integrated.md` 작성 (통합 Plan v2)
> - Plan v2 Jin 승인 후 본격 구현 착수

**✅ T12 완료**:
- Bot restart w/ exit learner unit fix (`f6679d95`)
- CAP max_hold 확장 (forex 120m / indices 150m / commodity 180m) + VIX retire (`46cd9c6a`)
- Phase 1 Exchange×Group composite (max_hold learner + exit_fsm) (`0f8ddd5e`)
- 진화 아키텍처 plan (`d322feef`)
- Noise 75건 quarantine (DB soft delete)

**🎯 다음 세션 (fresh token budget, 순서대로)**:

| # | 작업 | 예상 시간 | 의존성 |
|---|---|---|---|
| P0 | Phase 1.5 Event Bus + AI audit (learner / sustained-loss / regime flip) | 4-5h | 독립 |
| P1 | Phase 1.3 Signal hygiene (quality_gate filter + dedup + normalize) | 2-3h | 독립 |
| P2 | Phase 1.4 null_strategy learner filter + realtime anomaly guard | 1-2h | 독립 |
| P3 | Phase 1 composite 확장 (profit_cap / trail_mult / bep_activate 도 composite) | 3-4h | Phase 1 |
| P4 | Phase 2 Liquidity layer (분봉 → ticker_baseline) | 4-6h | Phase 1.3 |
| P5 | Phase 2.5 Position Health Score (6-factor) | 8-10h | P0 + P4 |
| P6 | 48h 관찰 + KPI 검증 | 대기 | 전체 |

**KPI 목표 (T12 baseline → 다음 세션 종료)**:
| 지표 | T12 start | T12 end (현재) | 다음 목표 |
|---|---|---|---|
| 북극성 asymmetry | +.32/-.33 | +.310/-.292 | winner ≥ 1.5× |loser| |
| CAP forex WR | 12% | 7-10% | ≥ 25% |
| BEP→loser 전환 | 84/24h | 관측 중 | ≤ 30/24h |
| TRAIL retention | 42% | 관측 중 | ≥ 65% |
| TIME 비율 (CAP) | 56% | 관측 중 | ≤ 35% |

## Phase 1.5 — AI Audit Event Bus (T12 추가)

**목적**: Learner / regime / sustained-loss 이벤트 발생 시 AI sanity check.

**Event Bus 통합**:
| Event | Trigger | AI Audit? | Debounce |
|---|---|---|---|
| learner_fire | preg Δ ≥ 10% | YES | 5min |
| regime_flip | crisis ↑↓ / group flip | YES | 10min |
| sustained_loss_4h | (ex×grp) 4h 연속 net-negative | YES (TUNE) | 1h |
| sustained_loss_8h | (ex×grp) 8h 연속 net-negative | YES (PAUSE+ALERT) | once |
| ticker_3_loss | 동일 ticker+direction 3연속 loss | auto-retire | immediate |
| session_2sigma | 세션 loss > 2σ | YES | once/session |
| kpi_breach | winner/loser asymm 2h 연속 ≤1.5 | YES | 1h |
| composite_key_new | 신규 `{ex}_{grp}` key 첫 생성 | YES | once/key |

**AI Prompt (짧음, Gemini Flash)**:
```
Event: {type}
Data: {ctx}
Output: GO | REVERT | TUNE(new_val) | RETIRE | PAUSE | ALERT
Reason: ≤1 sentence
```

**Budget**: Event-gate + debounce 로 예상 50-100 call/일 = $0.05-0.10 (총 budget $3 의 3%).

**구현** (`invasion/ai/event_audit.py`):
- Event queue (in-memory ring buffer)
- Gate + debounce
- AI dispatch
- Action executor (pset revert / retire / pause)
- Audit log → `ai_event_audits` table

---

## Phase 2.5 — Position Health Score (PHS) + 진화

**목적**: Open position 을 반사적 (price-only) 이 아닌 **예측적 (multi-factor)** 관리.

**PHS 공식 (0.0 ~ 1.0, 1.0 = healthy)**:
```
PHS = weighted_avg({
  price_score:     pnl / risk_budget + peak_retention,
  time_score:      expected_hold - actual_hold (norm),
  signal_score:    entry signal 재검증 (live recompute),
  liquidity_score: spread_now / spread_entry (분봉),
  correlation:     1 - cohort_corr_drift,
  regime_score:    regime_flip_since_entry (0 or 1),
})
```

**Tier 반응**:
| PHS | Action |
|---|---|
| > 0.7 | normal (현행) |
| 0.5 ~ 0.7 | log only |
| 0.3 ~ 0.5 | trail_mult ×0.8 (조기 수확 준비) |
| 0.1 ~ 0.3 | AI consult (GO / SCALE / CUT) |
| ≤ 0.1 | force exit + Jin alert |

**진화 피드백**:
- PHS 각 factor 가중치 (weighted_avg) 를 **learner 로 자동 튠**
- (exchange × group) 별 PHS factor 조합 독립 학습
- PHS 예측치 vs 실제 exit pnl correlation 측정 → 정확도 ↑ 방향 진화

**재료 (이미 있음)**:
- 분봉 → liquidity_score
- regime history → regime_score
- signal provider output → signal_score
- ticker_baseline normalize → 전부 scale-independent

**저장**:
- `position_health` 테이블 (pos_id, tick_ts, phs, factors JSON, action_tier)
- tick 별 async 업데이트 (5s debounce)

**구현 경로**:
1. `invasion/trade/position_health.py` — PHS 계산 엔진
2. `exit_cycle.py` 에 PHS hook (tick 단위)
3. `learner_phs_weights.py` — 가중치 자동 튠 (hourly)
4. AI consult hook ← Phase 1.5 Event Bus 재사용

---

## 통합 진화 아키텍처 (T12+)

```
┌──────────────────────────────────────┐
│  Live Open Position (tick-level)     │
│  ├─ PHS 계산 (6-factor)              │ ← Phase 2.5
│  ├─ 3-layer exit_params recalc       │ ← T11 완성
│  └─ (ex×grp×liquidity) composite 읽기│ ← Phase 1+2
└────────┬─────────────────────────────┘
         │ event (PHS drop / breach / learner fire)
         ▼
┌──────────────────────────────────────┐
│  Event Bus (debounced)               │ ← Phase 1.5
│  ├─ Learner change audit             │
│  ├─ Sustained loss monitor           │
│  ├─ Regime flip verify               │
│  └─ Cohort/correlation risk          │
└────────┬─────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────┐
│  AI Consultant (Gemini Flash, gated) │
│  └─ GO | REVERT | TUNE | RETIRE |    │
│     PAUSE | CUT_NOW | ALERT          │
└────────┬─────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────┐
│  Action Executor                     │
│  ├─ pset revert / tune               │
│  ├─ ticker_blacklist add             │
│  ├─ force exit (EMERGENCY exit_type) │
│  └─ Jin alert (console + MD log)     │
└──────────────────────────────────────┘
         │
         ▼ feedback
┌──────────────────────────────────────┐
│  Hourly Learner (진화)                │
│  ├─ PHS factor weights              │
│  ├─ Event debounce windows          │
│  ├─ AI consult thresholds           │
│  └─ Exchange×Group×Liquidity 계수    │
└──────────────────────────────────────┘
```

**북극성 정합**:
- Scale-independent ✅ (normalize API)
- Data-driven ✅ (모든 threshold learner 튠)
- Realtime ✅ (tick-level PHS)
- Aggressive amplify ✅ (boost winner, cut loser only)
- No hardcode ✅ (모든 값 evolvable)

**우선순위 (다음 세션)**:
1. Phase 1.5 Event Bus + AI audit (~4h, 가장 즉효)
2. Phase 2 Liquidity layer (~4h)
3. Phase 2.5 PHS (~8h, 가장 큰 작업)
4. Phase 3 48h 관측 검증

## 참조
- `.claude/docs/north_star.md` 영속 원칙
- `tasks/observation_log_t12.md` — hourly 관찰 로그
- T11 handoff: `memory/handoff_unified_2026_04_21_T11_northstar_dynamic.md`
- `invasion/ticks/hourly_stats.py` — 6 learner
- `invasion/strategy/ticker_baseline.py` — normalize API
