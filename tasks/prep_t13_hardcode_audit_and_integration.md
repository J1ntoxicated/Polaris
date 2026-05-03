# Prep T13 — 하드코딩 전수조사 + 신/구 구조 통합 설계 준비

> 다음 세션 (T13) fresh token 으로 본격 실행하기 전의 **사전 준비 문서**.
> Plan: `tasks/plan_t12_exchange_group_liquidity.md` 와 짝.
> 원칙: `feedback_no_hardcode_in_plans` (Plan/구현 모두 magic number 금지).

## 🚀 T13 첫 5분 Quick-Start

1. 본 문서 목차 전체 훑기 (Part A ~ I)
2. `tasks/plan_t12_exchange_group_liquidity.md` 읽기
3. `tasks/observation_log_t12.md` 읽기 (T12 findings)
4. `memory/handoff_unified_2026_04_22_T12_session_end.md` 재확인
5. **Part I Step -1** — Harness / Agent pool / Skill tree audit → `tasks/harness_audit_t13.md` 생성
6. Jin 승인 후 Part D D0 (Taxonomy v1) 착수

## 🎯 T12 실측 기반 T13 필수 적용 포인트 (관측 증거 → Part 연결)

> Jin: "실질 적용 부분 잘 생각해서 plan 에 적절히 표시". 아래는 T12 관측 증거가 직접 뒷받침하는 T13 실행 항목. 단정 아닌 "실측 증거 있음" 의미.

| # | 관측 증거 (T12) | T13 실행 Part | 우선도 |
|---|---|---|---|
| E1 | OKX +$720 → -$1048 batch exit (Thu 09:26-19:26) | **Part O L0** (timer 폐지 + 복합 원인 forensic) + **K.10** (real-time PHS) + **Phase 2.5** | 🔴 최우선 |
| E2 | 모든 open 의 `max_profit_pct=0` DB flush 누락 | **K.10** Position Live Monitor (tick flush) + **G1** DB 스키마 | 🔴 최우선 |
| E3 | Stuck 재누적 28h 주기 (0 → 100 반복) | **Part O L0** + **H.2** Reconciliation duplicate rule | 🔴 최우선 |
| E4 | CAP WR 0-23% 박스권 + short 18/long 5 bias | **Part M** direction 결정 로직 audit + **Phase 1.3** signal hygiene + **B.3** per_exchange taxonomy | 🟡 조사 우선 |
| E5 | Signal acted→entry 99% drop (Asia session) | **D14.5** Lag KPI 집계 + signal pipeline trace + **H.1** trace_id | 🟡 조사 우선 |
| E6 | CAP DB 중복 open (20 unique → 67 rows) | **H.2** Reconciliation duplicate open rule | 🟡 조사 우선 |
| E7 | OKX batch exit 원인 규명 불가 (trace 없음) | **H.1** trace_id + `trade_events` 구조화 이벤트 | 🔴 최우선 (모든 forensic 선결) |
| E8 | Signal `reason`='quality_gate:low_wr' score -48.9 혼입 | **D14.1** signal_blocks 분리 + **Part L** 잔여 뼈대 | 🟡 |
| E9 | Provider score scale 불일치 (CAP -16.7 vs Alpaca -0.6) | **B.3** Taxonomy per_exchange provider_score variants | 🟡 |
| E10 | 분봉 286MB 미사용 | **Part 2 Liquidity** + **K.2 Tier 1 지표** (분봉 → cell) | 🟢 기회 |
| E11 | Alpaca 미장 close 10h+ static | **J.11** Unfillable-Queued 상태 설계 검증 | 🟢 관찰 |
| E12 | OKX cash cow 가설 반증 (14h +$1047 → 10h -$1768) | **T14+ 후속 plan** (single-window 분류 금지, long-window 필수) | 🔵 T14 |
| E13 | Cleanup 의 KPI 회복 28h 지속 후 붕괴 | **T13 전체 리뷰 질문**: cleanup 주기성 재현 여부 | 🔵 |
| E14 | Exit learner unit bug 재현 방지 | **B.3** Taxonomy 런타임 validator + **E.1 D축** 구조결함 자가감지 | 🔴 |
| E15 | Jin 명시 원칙 3종 (no_quick_patch / flow / single_review_verdict) | 전 Part 에 **E.1 Per-Change Gate 4축** 강제 | 🔴 |

### T13 첫 작업 (E 증거 → 실행 순서 권장)

1. **E14 / E15 준수 환경 확보** — Per-Change Gate + no-hardcode lint 활성화 (Step -1 Harness audit 에 포함)
2. **E7 H.1 Trace 선결** — 이후 모든 forensic 의 기반
3. **E1/E2/E3 실측 forensic** — OKX 붕괴 timeline + stuck 주기 실증 (가설 재정의 X, data 확인 O)
4. **E4/E5/E6 조사** — direction bias / pipeline lag / DB 일치성
5. **구조 구현 순서** — Part D D0~D22 에 위 증거 반영
6. **E10 Liquidity / E11 Unfillable / E12 장기 검증** — 단계 4 이후

### 이 표 사용 규칙 (단정 방지)

- E# = 관측 증거 ID, T13 에서 해결해야 할 **실측 기반 확인 대상** 으로만 사용
- "이게 원인이다 / 이렇게 고친다" 단정 X
- T13 에서 각 E# 를 **전체 카드와 교차** 후 plan v2 확정
- feedback_no_single_review_verdict 준수

## 🔗 T14+ 후속 문서 (T13 구조 변경 이후)

- **`tasks/next_plan_t14_performance_classification.md`** — Exchange / Cell 성과 분류 + 자본 allocation / risk budget / strategy pool / signal weight / Paper→Live 전환 기준 설계 반영. T13 구조 완료 후 T14 에서 리뷰·결정.

## 📋 전체 Part 맵 (T12 종료 최종 상태)

| Part | 주제 | 상태 | 핵심 |
|---|---|---|---|
| A | 하드코딩 전수조사 전략 | spec 완료 | grep/AST 패턴 + 분류 태그 (TRADING/STRUCTURAL/PHYSICS/SEED/LEGACY) |
| B | 신/구 매핑 | spec 완료 | — |
| B.1 | (ticker, exchange) pair-keyed primary | spec | 모든 ticker-level 테이블/preg pair PK |
| B.2 | Multi-Matrix Cell (8축) | spec | CellKey + cell_resolve + lifecycle + 분리 배포 원칙 |
| B.3 | 🔴 Input Taxonomy + Unit Contract | spec 최우선 선결 | per_exchange variants + runtime validator |
| C | Plan v2 체크리스트 | 8개 조항 | — |
| D | 실행 순서 D0~D22 | Codex 반영 | 토대→설계→MVP→확장→안정 5단계 |
| E | 제약 | E.1 Per-Change Gate 4축 (A북극성/B타당성/C feedback/D 구조결함) |
| F | 5-stage pipeline × 북극성 | spec | Selection→Execution→Management→Exit→Post-eval |
| G | 3-Tier 프로세스/DB 분리 | spec | ingest / trade / learn 독립 프로세스 |
| H | Cross-cutting 11개 | 🔴 5개 T13 필수 | Observability / Reconcile / Canary / Kill / Backup |
| I | Harness 사전 audit | Step -1 | Plan v2 수용 기준 먼저 확정 |
| J | 🟢 Flow Amplifier | spec | skip/block 금지, size 확장 + Unfillable-Queued |
| K | Opportunity Detection & Sensitivity | K.1~K.11 | 지표 3-tier + exchange-specific + live monitor |
| L | (축소, M 흡수) | 레퍼런스 | 선행 뼈대만 유지 |
| M | 🟢 Dynamic Signal Generation | spec | 5-layer (composition/regime/genetic/meta/AI) |

## 💡 T12 세션 관찰 기록 (판단·결정 X, T13 전체 리뷰 시 재평가 대상)

> **주의**: 아래는 관찰만. "재료 있으니 쉽다" / "어느 쪽이 우선" 같은 단정 금지.
> T13 세션 시작 시 **모든 카드 펼치고 전체 리뷰** → 그 시점에 "새로 만들기 / 변경 / 활성화" 결정.
> **`feedback_no_single_review_verdict`**: 1회 리뷰 / debate / 관측 = 부분 증거. **나무 말고 숲**. 모든 카드 동시 펼침 후에만 결정.

### 관찰 1. 데이터로 측정된 현상
- Cleanup 172건 직후 KPI 단기 악화 → 24h 내 회복 패턴 관측 (재현 여부 미확인)
- CAP 20 unique ticker → DB 67 rows, Alpaca 91 → 93 rows `status='open'` 중복 존재
- Alpaca 미장 첫 h WR 28% +$1181 달성 기록 있음
- OKX paper DB UPDATE 만으로 포지션 정리 가능 확인
- 미장 open 직후 30min window 에서 asymm 1.64× 기록 (샘플 작음)

### 관찰 2. 세션 중 사용해본 방법론 (효과·위험 미평가)
- 관측 전용 .sql + hourly cron 패턴 사용함
- Quarantine soft tag (`status='quarantined_*' + exit_type='CLEANUP_*_T*'`) 태그 사용함
- Sample-based gate 표현 사용 (time-based 대비)
- Paper vs Live broker 분기 script 작성함
- Codex peer review 1회 수행 — blind spot 몇 건 catch

### 관찰 3. 존재 확인된 재료 (사용 여부·적합성은 T13 판단)
- 분봉 파일 `data/candles/*_MINUTE_5.json` 약 286MB
- `trades.realized_slippage_bps` 컬럼 존재
- `signals.reason` 에 quality_gate 거부 메시지 기록됨
- `ticker_baseline.normalize()` API 존재
- `strategy_cell_matrix` 테이블 존재
- `ai_calls` / `ai_decisions` 테이블 존재
- `positions_snapshots` 테이블 존재
- `hour_stats` 테이블 + 6 learner hourly 집계 존재

### 결함 8 (관측 추가)
- DB 에 동일 ticker 로 `status='open'` 중복 레코드 발견
- 출처: T12 cleanup 시 unique ticker 수와 DB row 수 불일치
- T13 에서 reconcile 로직 재설계 범위 결정 필요

### T13 리뷰 시 확인 대상 질문 (답 없음, 단순 프롬프트)
- cleanup 의 KPI 회복 패턴이 재현되는가? 단발성인가?
- "loser tail 축 제거" vs "winner amplify" 우선순위는?
- 기존 테이블 재사용이 실제로 가능한지 (스키마 적합성 조사 필요)
- Paper → shadow mode 모델 전환이 M.4 와 정합하는지
- 관측 방법론이 앞으로도 유효한가 (bias 위험 있는가)

### T12 후반 추가 관측 (2026-04-23 Thu, 가설 update)

**추가 확인 1 — OKX 붕괴 원인 재검토**:
- 내 초안 가설 "Tue 미장 cohort 24h timer" = **지지 안 됨** (13/14:00 Thu entry 소수, 대부분 분산)
- Batch-exit 은 발생했으나 **단순 단일 cohort 아님**
- 복합 원인 가능성: 시장 변동성 / STOP 클러스터링 / PHS 없어 동시 degradation / 개별 TIME 우연 겹침
- T13 Part O 설계 시 "timer 폐지만으로 충분치 않을 수 있음" 경계

**추가 확인 2 — CAP contrarian 가설 약화**:
- CAP forex 24h: short 18건 / long 5건 (contrarian long 이어야 하는데 short majority)
- 양방향 모두 loss (short -0.042% / long -0.065%)
- 내 "trend-following × contrarian 미스매치" 단순 설명 **정확도 낮음**
- 실제는 direction 결정 로직 / quality_gate 가 **short bias 만드는 원인** 조사 필요

**추가 확인 3 — Signal→direction 매핑 audit**:
- North star "fear → long" 원칙이 실제 entry direction 에 반영되는가?
- Direction 결정 단계 어디서 bias 발생하는가?
- T13 Phase 1.3 / Part M / B.3 Taxonomy 에 연결 대상

### T12 후반 plan 영향 (추가 확인 대상, 단정 X)
- **Part O L0 확장**: timer 폐지 + 복합 batch 원인 진단 (시장 변동성 / STOP 클러스터 / PHS 동시 degradation)
- **Part M / Phase 1.3 연결**: direction 결정 로직 audit 추가. Signal→direction 매핑 단계 trace
- **T13 초기 forensic**: 실제 OKX 붕괴 timeline 정밀 분석 (entry/hold/exit 단계별) — 가설 재정의 없이 실측 기반

---

## 🔴 결함 1~7 통합 (T12 관측 → T13 fix 경로)

| # | 결함 | 출처 | T13 해결 |
|---|---|---|---|
| 1 | `max_profit_pct=0` DB flush 누락 | anomaly_snapshot | K.10 Position Live Monitor |
| 2 | TIME→TRAIL_PROTECTED 무한 suppression | anomaly_snapshot | exit_cycle 재설계 + Phase 2.5 PHS |
| 3 | alpaca asset_group=crypto 분류 오류 | anomaly_snapshot | Phase 1.3 group 정규화 |
| 4 | Drop threshold global (ex 무관) | Part L | M.1 active_provider_set (대체) |
| 5 | Drop = 데이터 소실 (flow 위반) | Part L | D14.1 signal_blocks 이관 |
| 6 | Provider score scale 미정규화 | Part L | B.3 taxonomy per_exchange variants |
| 7 | OKX signal coalescing 부재 | Part L | G1 Tier 1 tick-level dedup |
| 8 | DB 동일 ticker 중복 `status='open'` | T12 cleanup 관측 | H.2 Reconciliation job 에 "duplicate open" rule 추가 |
| 9 | Signal acted_on → entry 사이 99% drop (불투명 gate 존재) | T12 Asia session | Lag KPI D14.5 + signal pipeline trace |
| 10 | Batch-exit (실시간 추적 부재, timer 의존) | T12 OKX -$1023/2h | Part O L0 PHS + K.5 Fast-Out + TIME timer 폐지/종속 |

## 🎯 T13 성공 정의

다음이 모두 완료되면 T13 성공:
- [ ] `docs/metric_taxonomy.yaml` v1 (Part B.3)
- [ ] `audit_t13_hardcoded.md` + `audit_t13_units.md`
- [ ] Unit BUG 전부 fix + regression 통과
- [ ] `plan_t13_integrated.md` — Plan v2 (Jin 승인)
- [ ] 최소 구현: 3-Tier DB 스키마 G1 + Multi-Matrix Cell M1~M3 + H.1/H.2/H.5/H.10 skeleton
- [ ] 봇 restart + 24h 관측 → KPI asymmetry 유지/개선

## 목표

다음 세션 T13 첫 1-2h 안에 다음 3가지 산출물 확보:
1. **하드코딩 전수조사 리포트** — invasion/ 내 모든 magic number 분류표
2. **신/구 구조 매핑** — 기존 6 learner / exit / entry / signal 각 요소와 신규 Phase 1.5~2.5 의 시너지/중복/충돌 맵
3. **통합 Plan v2** — Phase 순서 재조정 + 의존성 + 시너지 활용 + 하드코딩 제거 포함

## Part A — 하드코딩 전수조사 (감지 전략)

### 대상 범위 (scope)
- `invasion/**/*.py` 전체
- `data/live_config.json` 외부 override 값
- `data/*.json` (candles 제외, config 성격만)
- `.claude/docs/*.md` 설계 문서 (spec 내 숫자 체크)

### 감지 패턴 (grep / ast 조합)
| 유형 | Regex / AST | 예시 (수집 대상) |
|---|---|---|
| 실수 상수 (함수 내) | `\b\d+\.\d+\b` 가 `_reg` / 테스트 / 상수 선언 외 위치 | `mult * 0.8` |
| 정수 threshold | `\b(if|elif)\s.*[<>]=?\s*\d+\b` (리터럴 비교) | `if n < 20:` |
| 시간 윈도우 | `\b\d+\s*\*?\s*(60|3600|86400)\b` | `7*86400`, `604800` |
| 비교 배율 | `\*\s*\d+(\.\d+)?\b` / `/\s*\d+(\.\d+)?\b` | `cur * 1.5`, `/ 10` |
| 범위 튜플 | `\(\s*\d+(\.\d+)?\s*,\s*\d+(\.\d+)?\s*\)` 에서 `_reg` 외 | 비등록 bound |
| 문자열 enum list | `["\w+", "\w+", ...]` 함수 내 고정 | 그룹 순서 리터럴 |

### 분류 (각 매치에 태그)
| 태그 | 의미 | 조치 |
|---|---|---|
| `TRADING` | 매매 행동 영향 (threshold / size / hold / score) | **preg 로 이관 필수** |
| `STRUCTURAL` | SQL 컬럼명 / protocol / enum / index | 유지 |
| `PHYSICS` | 수학 상수 / unit 변환 계수 (60초=1분) | 유지 (코멘트 보강) |
| `SEED_DEFAULT` | `_reg(name, DEFAULT, ...)` 내부 | 유지 (이미 registry) |
| `TEST_ONLY` | 테스트 코드 내 | 유지 |
| `LEGACY_DEAD` | 호출 안되는 죽은 코드 | 삭제 후보 (dev-wire-guardian 검증) |

### 산출 포맷
```
tasks/audit_t13_hardcoded.md
- Summary: 총 N건, 분류 breakdown
- Top offenders: 파일별 TRADING 태그 건수 rank
- 각 항목: file:line / snippet / 태그 / 제안 preg key 이름 / 제안 seed / 제안 bounds / 제안 learner scope
```

## Part B — 신/구 구조 매핑 (시너지/중복/충돌)

### 매핑 대상 (기존 vs 신규)
| 기존 (T11까지) | 신규 (Phase 1.5~2.5) | 관계 유형 |
|---|---|---|
| 6 hourly learner | Meta-learner (learner 가중치) | **상위 제어** |
| exit 3-layer recalc | PHS tier action (tighten/cut) | **시너지** (PHS 가 recalc 입력) |
| cell_matrix score | Position Health Score | **보강** (entry score × live PHS) |
| regime_mult (size) | Regime-aware exit amp | **확장** (size → exit 도) |
| signal funnel | Signal dedup + scale normalize | **정화** |
| AI proactive_exit (disabled) | Event Bus AI audit | **대체** (event-gated) |
| quality_gate | Signal blocks 분리 + Event Bus | **분리+통합** |
| ticker_baseline (5-metric) | Liquidity layer (신규 metric) | **확장** (6th metric) |
| ticker_dynamics | PHS signal_score / liquidity_score 저장소 | **재사용** |
| consecutive_loss_halt | Sustained loss monitor | **시간축 추가** |
| strategy_evolution (Elo) | Pyramiding / position rotation | **독립 but 자본 공유** |

### 시너지 포인트 (T13 에서 통합 설계 필수)
- **PHS ⇄ Event Bus**: PHS 급락 = Event 1종
- **PHS ⇄ Live recalc**: PHS factor 가 recalc 공식 입력
- **Composite key ⇄ PHS factor weights**: (ex × grp) 별 weight 독립
- **Liquidity layer ⇄ Signal normalize**: 분봉 volume 은 양쪽 재료
- **Meta-learner ⇄ AI audit**: learner 기여도 ↔ AI GO/REVERT 기록
- **Pyramiding ⇄ PHS**: PHS 높은 포지션만 pyramid 자격
- **Position rotation ⇄ Cohort risk**: 약한 포지션 cut → 강자 pyramid 자본

### 중복/충돌 주의 지점
- `proactive_exit` (disabled) vs `Event Bus AI audit` — 완전 대체, 구 로직 제거
- `bep_activate` (global) vs `PHS tier action` — 중첩 실행 금지, 우선순위 정의
- `time_exit_max_age_sec_{group}` fallback vs composite — T12 에서 이미 fallback 체인 있음, 그대로 유지
- `max_profit_pct` 단위 — T12 fix 이후 일관 %, 신규 코드 동일 단위 준수

## Part B.1 — (ticker, exchange) pair-keyed Primary (Jin T12 확정)

**원칙**: 모든 ticker-level / ticker-sensitive 데이터의 primary key 는 **(ticker, exchange) pair**. 같은 ticker 라도 exchange 별 특성 (spread / tick freq / 유동성) 다름 → 독립 관리.

**영향 범위 (T13 에서 스키마 + API 확장)**:

### DB 스키마 (pair PK 로 마이그)
| 테이블 | 현재 PK | 변경 | 마이그 방식 |
|---|---|---|---|
| `ticker_baseline` | ticker | (ticker, exchange) | ALTER + backfill exchange |
| `ticker_dynamics` | ticker | (ticker, exchange) | 동일 |
| `ticker_performance` | ticker | (ticker, exchange) | 동일 |
| `ticker_stats` | ticker | (ticker, exchange) | 동일 |
| `ticker_blacklist` (preg list) | ticker 문자열 | `{exchange}:{ticker}` 또는 JSON pair | live_config key 변경 |

### preg key schema
- composite chain: `{base}_{exchange}_{ticker}` → `{base}_{exchange}_{group}` → `{base}_{exchange}` → `{base}_{group}` → `{base}`
- `get_xg` 확장: `get_xtg(base, exchange, ticker=None, group=None)` — 우선순위 4단
- Learner scope 자동:
  - n ≥ seed `preg_learner_sample_min_ticker` → ticker-level 새 key 생성
  - n ≥ seed `preg_learner_sample_min_group` → group-level
  - n ≥ seed `preg_learner_sample_min_exchange` → exchange-level
  - 미달 → global fallback

### API 확장
- `normalize(ticker, exchange, metric, raw)` — 현재 `(ticker, metric, raw)` 확장
- Baseline 계산 시 exchange 필터 자동 (cross-exchange 오염 차단)
- Signal score scale 도 exchange-aware (CAP score range ≠ OKX score range)

### 미구현 Rationale (현재는 OK)
- 현재: 1 group = 1 exchange (실질 충돌 없음)
- 미래 대비 + 개념적 정합 위해 schema 통일
- T13 Part A audit 에서 발견되는 매직넘버 중 exchange 가정 박힌 것 (예: "crypto means OKX") 전부 pair 로 refactor

## Part B.2 — Multi-Matrix 확장 구조 (핵심 설계)

**문제**: 축 추가 = cell 수 기하급수 (dimension explosion) + sparse sample. 구조 없이 하면 preg key 수만 N 만 개, 학습 불가.

### 축 표준 선언 (Canonical Dimensions)

**순서 고정 (중요 — 키 인코딩 결정)**:
```
DIMS = [exchange, group, ticker, session, regime, direction, liquidity_tier, strategy_id]
```
- Depth 0 = global (`*`)
- Depth 1 = exchange only
- Depth 2 = exchange + group
- ...
- Depth 8 = 전체 specified (가장 세밀)

**각 축의 sparsity 특성**:
| 축 | 카디널리티 | 보통 sample density |
|---|---|---|
| exchange | 3-5 | 높음 |
| group | 5-7 | 중간 |
| ticker | 수백~수천 | **매우 낮음 (sparse)** |
| session | 3-5 (Asia/EU/US + overlap) | 높음 |
| regime | 3-5 | 중간 |
| direction | 2 | 매우 높음 |
| liquidity_tier | 3-4 (micro/mid/large) | 중간 |
| strategy_id | 수십 | 낮음 |

### Hierarchical Cell Key 인코딩

```python
@dataclass(frozen=True)
class CellKey:
    metric: str        # "profit_cap", "trail_mult", ...
    exchange: str|None
    group: str|None
    ticker: str|None
    session: str|None
    regime: str|None
    direction: str|None
    liquidity_tier: str|None
    strategy_id: str|None

    @property
    def depth(self) -> int:
        return sum(1 for f in self.dims if f is not None)

    def parents(self) -> list["CellKey"]:
        """Depth 낮아지는 방향의 모든 fallback 후보 (중요도 순)."""
        # 가장 세밀한 축부터 * 로 치환해 올림
        ...
```

### Fallback Cascade (depth 역순 조회)

```python
def resolve(cell: CellKey) -> tuple[value, sample_n, fallback_depth]:
    for candidate in cell.parents():
        entry = REGISTRY.get(candidate.encode())
        if entry and entry.sample_n >= min_sample_at(candidate.depth):
            return entry.value, entry.sample_n, candidate.depth
    return GLOBAL_SEED, 0, 0
```

**샘플 임계 cascade** (전부 preg, 하드코딩 금지):
- `preg_cell_min_sample_depth_{depth}` — depth 별 최소 sample 요건
- 깊은 depth 일수록 요건 높음 (sparse 방어)
- 학습 진행 시 learner 가 이 임계 자체도 튠 가능

### Cell 생명주기 (자동 promote / demote)

| 상태 | 조건 | 전환 |
|---|---|---|
| **seed** | REGISTRY 에 선언만, sample 0 | learner 가 첫 data 수집 시 active |
| **active** | sample ≥ min_sample, 학습 진행 | 기본 상태 |
| **promote** | 하위 depth 에서 distinct pattern 감지 + sample 충분 | **자동 새 deeper cell 생성** |
| **dormant** | 일정 기간 no new sample | fallback 만 제공, 튠 중지 |
| **retire** | 장기 dormant + upstream 이 더 좋음 | 삭제 (registry clean) |

- 전환 임계 전부 preg (`preg_cell_promote_sample`, `preg_cell_dormant_days`, ...)

### Storage 설계

**기존 `strategy_cell_matrix` 테이블 확장**:
```
strategy_cell_matrix (
    metric TEXT,
    cell_hash TEXT,       -- CellKey encode (canonical)
    dims_json TEXT,       -- 원본 dim tuple (debug/query)
    depth INTEGER,
    value REAL,
    sample_n INTEGER,
    last_updated REAL,
    source_learner TEXT,
    state TEXT,           -- seed/active/promote/dormant/retire
    PRIMARY KEY (metric, cell_hash)
)
```

**인덱스**: `(metric, depth)`, `(metric, exchange, group)`, `(cell_hash)`

### Multi-Matrix = Multi-Metric × Hierarchical Cell

각 metric (profit_cap, trail_mult, bep_activate, max_hold, size_mult, phs_factor_weight_*, ...) 은 **자체 hierarchical cell tree** 를 가짐. 독립적으로 진화.

**Meta view**: Dashboard 는 metric × dimension heatmap 형태. 특정 cell 이 튀는지 시각화.

### API 수렴 (모든 learner / exit / size 공통)

```python
# 읽기 — 모든 trading code 가 이것만 사용
value = cell_resolve(metric, exchange, group, ticker, session, regime, direction, liquidity_tier, strategy_id)

# 쓰기 — learner 전용
cell_learn(metric, cell_key, new_value, sample_n)

# 진화 — lifecycle 자동
cell_lifecycle_tick()  # hourly, promote/demote/retire 판정
```

구 `_preg` / `get_xg` / `get_xtg` 는 이 API 로 일괄 대체 (점진). Phase 1 의 `get_xg` 는 Phase 2 cell API 로 승급.

### 구현 단계 (T13 세분화)

| Step | 작업 | 선행 |
|---|---|---|
| M1 | CellKey dataclass + encode/parents/depth | — |
| M2 | strategy_cell_matrix 스키마 확장 (ALTER + migration) | M1 |
| M3 | cell_resolve / cell_learn API | M2 |
| M4 | 기존 `get_xg` → `cell_resolve` 호환 shim | M3 |
| M5 | 6 learner 을 cell_learn 경유로 이관 | M4 |
| M6 | cell lifecycle tick (hourly promote/demote) | M5 |
| M7 | Dashboard heatmap (metric × dim) | M5 |

### 북극성 정합

- **Scale-independent**: metric 축 독립, cell 내 normalize 유지
- **Data-driven**: 모든 임계 preg, 자동 promote/demote
- **Realtime**: cell_resolve O(depth) = O(8) 이하 상수시간
- **No hardcode**: CellKey 축 순서만 구조적 상수, 모든 값/임계 preg
- **Amplify-only**: cell learn 시 winner cell 만 value 확장, loser cell 은 fallback 으로 회귀 (cell 단위 amplify)

### 분리 배포 원칙 (Codex 반영 + Jin 실용 조정: sample-based gate)

Sparse cell 과적합 + 단기 변동성 오인 방지. **아래 변경은 동시 배포 금지**:
1. `cell_resolve` 읽기 (M4)
2. `cell_learn` 쓰기 (M5 = 6 learner 이관)
3. Promote/demote lifecycle (M6)
4. Factor weight 학습 (K.4)

**Gate 조건 (time 기반 X, sample 기반 O — 모두 preg, exchange-aware)**:
- OKX (24/7, 고밀도): `preg_deploy_gate_sample_okx` 거래 확보 후 판정 (예상 1-2h 내)
- CAP (24/5, 중밀도): `preg_deploy_gate_sample_cap` 거래 확보 (평일 active 세션)
- Alpaca (미장만): 미장 open 이 아니면 Gate 대기 (skip 아님, 시간 도달까지 queue)
  - 미장 open 후 `preg_deploy_gate_sample_alpaca` 확보 시 판정
- Gate metric: Δ asymm ratio / Δ trade count / reconcile mismatch = 0

**실무 해석**:
- OKX/CAP 은 같은 세션 내 1-2h면 통과 가능 (평일 한 세션 내 전부 끝)
- Alpaca 는 미장 세션 1회 관측이면 완결 (AEST 23:30~06:00)
- "한 세션 안에 끝낼 수 있음" = Jin 실제 워크플로우 부합

Gate 실패 시: Event Bus alert + 다음 단계 대기 (전체 session block 아님, 해당 변경만 pending).

## Part B.3 — Input Taxonomy + Unit Contract (최우선 선결)

**Why**: T12 exit learner unit bug 재현 방지. Multi-Matrix Cell fallback 은 **동일 metric 의 모든 cell 이 같은 unit 으로 저장** 되어야 작동. 지금은 module 별로 unit 가정이 다를 수 있음 → silent drift 최대 위험.

> **이 작업이 Part B.2 Multi-Matrix 구조보다 먼저**. Taxonomy 없이 cell 쌓으면 fallback 의미 없음.

### B.3.1 Metric Taxonomy (모든 input/output 선언)

각 metric/signal 에 대해 **Contract card** 작성:

```yaml
metric: profit_cap
unit: percent      # 예: 2.5 = 2.5%
semantic: winner_expansion  # 클수록 winner 더 보호
range_normal: [0.5, 20.0]
range_hard: [0.1, 100.0]
source: learner._learn_profit_target
consumers:
  - trade/exit.py:compute_tp
  - trade/exit_fsm.py:profit_cap_check
normalize_policy: per_ticker_baseline  # or raw/zscore/minmax
null_handling: fallback_parent_cell
invalid_action: log + clamp_to_range_hard
sign_convention: positive_only
related: [trail_mult, bep_activate]  # 상호 의존
```

**선언 대상**: 
- 모든 preg key (437+ 현재)
- 모든 signal score / factor
- 모든 live-computed metric (pnl_pct, max_profit_pct, age, spread_bps, ...)
- 모든 PHS factor (Phase 2.5)
- 모든 normalize 결과

**저장**: `docs/metric_taxonomy.yaml` (single SSOT). Code 는 이 yaml 참조 (runtime 검증).

### B.3.2 Unit Categories (표준 분류)

| Category | 예시 | 변환 규칙 |
|---|---|---|
| **ratio** | 0.5, 1.2 (배율) | * 100 → percent |
| **percent** | 2.5 (=2.5%) | / 100 → ratio, * 100 → bps |
| **bps** | 25 (=0.25%) | / 10000 → ratio |
| **fraction** | 0.025 (=2.5%) | * 100 → percent |
| **absolute_usd** | 150.0 | — |
| **count** | 42 | — |
| **seconds** | 3600 | / 60 → min, / 3600 → hour |
| **minutes** | 60 | * 60 → sec |
| **zscore** | -1.5 ~ +1.5 | normalize 경유 |
| **boolean** | 0/1 | — |
| **enum** | "long"/"short" | — |

**위험 혼동 쌍**:
- `ratio` vs `percent` (×100 배 차이) 🔥 T12 exit bug 원인
- `percent` vs `bps` (×100 배 차이)
- `fraction` vs `percent` (×100 배 차이, 이름 유사)
- `seconds` vs `minutes` (×60 배)
- `usd` vs `usd_per_unit` (곱셈 여부)

### B.3.3 Runtime 검증 레이어

```python
# config/_metric_contract.py
@validate_metric("profit_cap")
def set_profit_cap(cell: CellKey, value: float):
    contract = TAXONOMY["profit_cap"]
    assert contract.range_hard[0] <= value <= contract.range_hard[1], \
        f"profit_cap out of hard range: {value}"
    if not (contract.range_normal[0] <= value <= contract.range_normal[1]):
        log_event("METRIC_WARN", f"profit_cap outside normal: {value}", "warn")
    # unit 검증: 만약 value > 100 이고 unit=percent → 99% 확률 unit mismatch
    _detect_unit_anomaly("profit_cap", value, contract)
```

**Auto unit-anomaly 감지**: value 가 normal range 의 ×100 / ×0.01 배로 벗어남 → unit mismatch 경고 (T12 bug 같은 것 즉시 잡힘).

### B.3.4 Audit 대상 (Part A 와 연동)

Part A 전수조사 시 **각 매직넘버에 unit tag 필수**:
- TRADING + UNIT_BUG (예: `*100` 이 unit 변환인지 임의 배율인지 모호)
- TRADING + UNIT_VERIFIED (contract card 와 일치)
- TRADING + UNIT_UNDEFINED (taxonomy 없음 → 선언 필요)

**특히 의심스러운 기존 코드 영역**:
- `max_profit_pct` 여러 모듈에서 unit 해석 제각각 (T12 fix 적용됨, 전수 재검증 필요)
- `pnl_pct` (% 또는 fraction? ← 코드 마다 다를 수 있음)
- `strength`, `score` (signal/strategy 모두 숫자인데 scale 다름)
- `atr` (가격 단위 vs percent 단위)
- `volume` (base currency 단위 vs USD 단위)
- `spread` (bps vs percent vs absolute)

### B.3.5 cell_resolve 와 통합

```python
def cell_resolve(metric: str, *dims) -> MetricValue:
    contract = TAXONOMY[metric]
    raw = _cascade_lookup(metric, dims)
    # unit 검증 (stored value 가 contract 와 일치하는지)
    _contract_check(contract, raw)
    return MetricValue(value=raw, unit=contract.unit, source_depth=...)
```

**읽기 쪽에서도 unit-safe**: consumer 코드는 `MetricValue` 받고 `.as_percent()` / `.as_seconds()` 등 변환 메서드만 사용. 맨날 숫자만 받던 코드 제거.

### B.3.6 Taxonomy 구축 단계 (T13)

| Step | 작업 | 산출 |
|---|---|---|
| T1 | 기존 preg 437+ key 의 unit/range 전수 declaration | `docs/metric_taxonomy.yaml` v1 |
| T2 | live-computed metric (pnl_pct 등) taxonomy 추가 | yaml v2 |
| T3 | runtime validator 구현 (_metric_contract.py) | 검증 layer |
| T4 | 기존 코드의 unit 가정 audit (Part A 확장) | `audit_t13_units.md` |
| T5 | unit mismatch 발견 점 전부 fix + regression test | PR 시리즈 |
| T6 | cell_resolve 에 unit-safe wrapper 통합 | API 확정 |

### B.3.7 북극성 정합

- **Scale-independent**: taxonomy 가 normalize 의 선결조건
- **Data-driven**: 검증 failure 도 event → AI audit 대상 가능
- **No hardcode**: range/unit 도 yaml 에서 override 가능 (preg 로 등록)
- **Sensitive safety net**: Jin 언급 — unit bug 한 건이 전체 학습 방향을 틀게 만듦. Taxonomy 가 이 single point of failure 제거.

## Part C — 통합 Plan v2 체크리스트

다음 세션에서 Plan v2 작성 시 반드시 포함:

- [ ] **Input Taxonomy + Unit Contract** (최우선 선결) — Part B.3 `docs/metric_taxonomy.yaml` + runtime validator + unit mismatch 전수 fix
- [ ] **Multi-Matrix Cell 구조 적용** — Part B.2 CellKey + cell_resolve API + hierarchical fallback + lifecycle 자동화
- [ ] **(ticker, exchange) pair-keyed primary** — CellKey 축 중 ticker+exchange 축 모든 ticker-level 테이블/preg/API 에 exchange 축 필수
- [ ] **하드코딩 0건 증명** — audit 리포트 링크 + 남은 TRADING 태그 = 0 확인
- [ ] **Unit audit 0건 UNDEFINED / 0건 BUG** — 모든 매직넘버 unit tag + contract card 일치 증명
- [ ] **3-Tier 아키텍처 기반 확보** (Part G) — DB 스키마 경계 + Tier 1 독립 프로세스 + data flow contract
- [ ] **Flow Amplifier 구현** (Part J) — 모든 신호 진입 + size 흐름 확장 + trade-to-fee ratio KPI 추적 (차단/skip/reject 금지)
- [ ] **Sensitivity Expansion** (Part K) — 신규 indicator Tier 1 즉시 도입 + multi-factor 가중치 cell 학습 + peak capture / missed opportunity KPI + fast-in/out latency 최적화
- [ ] **preg 신규 키 목록** — 이름 / seed / bounds / learner scope / fallback chain
- [ ] **(exchange × group) composite 확장 대상 전부** — profit_cap, trail_mult, bep_activate 등 모든 learner
- [ ] **PHS factor weight preg 키** — 각 factor (price/time/signal/liquidity/correlation/regime) 의 가중치 = learner 튠
- [ ] **PHS tier boundary preg** — 등급 경계값 전부 preg (현재 plan 의 0.1/0.3/0.5/0.7 → 전부 preg)
- [ ] **Event Bus 디바운스 / 임계값 preg** — 시간 / 비율 / 카운트 전부 preg
- [ ] **시너지 활용 매트릭스** — Part B 매핑을 코드 hook 포인트로 번역
- [ ] **충돌 제거 증명** — 중복 로직 제거 / 우선순위 결정 기록
- [ ] **의존성 그래프** — Phase A 완료 없이 Phase B 실행 불가 표시
- [ ] **Rollback plan** — 각 Phase 배포 실패 시 되돌리는 절차

## Part D — T13 실행 순서 (최종, 토대 → 확장 → 구현)

### 단계 1: 토대 확보 (Foundations) — 6-8h

| 순서 | 작업 | 예상 | Why first |
|---|---|---|---|
| **D0** | **Part B.3 Taxonomy v1** → `docs/metric_taxonomy.yaml` | 2-3h | Unit 기반 없이 audit/cell 무의미 |
| **D1** | Part A 전수조사 (unit tag 포함) → `audit_t13_hardcoded.md` + `audit_t13_units.md` | 2-3h | Taxonomy 기반 unit mismatch 동시 감지 |
| **D2** | Unit BUG 즉시 fix + regression test | 1-2h | Silent drift 차단 |

### 단계 2: 구조 설계 확정 (Blueprint) — 2-3h

| 순서 | 작업 | 예상 |
|---|---|---|
| **D3** | Part B / B.1 / B.2 / G hook 포인트 grep 검증 → 매핑 확정 | 1h |
| **D4** | Part C 체크리스트 기반 **Plan v2** (`plan_t13_integrated.md`) | 1.5-2h |
| **D5** | Jin 리뷰 + 승인 | — |

### 단계 3: 최소 구현 (MVP) — 15-20h — **Codex 리뷰 반영 순서 (D6→... 재배치)**

**원칙**:
- trace 없이 reconcile 하면 불일치 추적 불가 → trace 먼저
- Tier 1 분리 전에 Cell API 들어가면 PID rotation 해결 전 학습경로 복잡화 → Tier 1 먼저
- Backup 은 restore 리허설까지 닫아야 의미

| 순서 | 작업 | Part | 예상 | Rationale |
|---|---|---|---|---|
| **D6** | H.5 Kill Switch (`touch data/KILL`) | H.5 | 2h | 안전망 최우선 |
| **D8** | H.1 trace_id + `trade_events` 구조화 이벤트 | H.1 | 3h | 후속 모든 추적의 근간 |
| **D9** | H.2 Reconciliation job (broker ⇄ DB) | H.2 | 3h | trace 기반으로만 의미 |
| **D7** | H.10 DB Backup 자동 스냅샷 | H.10 | 1.5h | 스키마 바꾸기 전 확보 |
| **D7.5** | **Backup Restore 리허설** (snapshot restore + WAL reopen + trade resume 검증) | H.10 확장 | 1h | Codex: 복구 동작 증명 없이는 backup 무의미 |
| **D10** | G1 DB 스키마 확장 (`market_ticks` / `candles_*` / `provider_raw` / `feature_cache` / `ai_event_audits` / `position_health` / `lag_kpi_hourly`) | G + K.3 + K.10 | 3h | Tier 1 프로세스 분리 전제 |
| **D17** | G3+G4 Tier 1 독립 프로세스 (`invasion-ingest`) + supervisor | G | 4-5h | Codex: PID rotation 원인 제거 먼저 |
| **D11** | M1~M3 Multi-Matrix Cell (`CellKey` + `cell_resolve` + `cell_learn`) | B.2 | 3-4h | 안정된 Tier 1 위에서 학습경로 재편 |

### 단계 4: Phase 확장 (학습/안전) — 20-28h

| 순서 | 작업 | Part | 예상 |
|---|---|---|---|
| **D12** | H.4 Canary + KPI Guard | H.4 | 3h |
| **D13** | Phase 1.5 Event Bus + AI audit (D8 trace_id 재사용) | plan_t12 | 4-5h |
| **D14** | Phase 1.3 Signal hygiene — `signal_blocks` 스키마 분리 + dedup + normalize (quality_gate 오염 closed loop) | plan_t12 | 3-4h |
| **D14.5** | **Lag KPI 집계 job** — `lag_kpi_hourly` 채우기 (signal emit→entry, entry→peak, peak→exit latency) | Codex 제안 | 2h |
| **D15** | Phase 1.4 null_strategy filter + H.3 Auto Data QA | plan_t12 + H.3 | 2h |
| **D16a** | M4 cell_resolve shim (읽기 경로 이관, 쓰기 동일) | B.2 | 2h |
| **D16.5** | **분리 배포 Gate** — D16a 배포 후 sample 확보 시 KPI 확인 (OKX/CAP: 1-2h, Alpaca: 미장 1 세션). 악화 없음 → D16b. 동시 세션 내 완결 가능 | B.2 | 1-2h |
| **D16b** | M5 6 learner → cell_learn 이관 (쓰기 경로) | B.2 | 2h |
| **D18** | Phase 2 Liquidity layer (G1 이후 분봉 DB 에서 readable) | plan_t12 | 4h |
| **D18.5** | Part J Flow Amplifier core (size 확장 + flow envelope 3축) | J | 4h |
| **D18.6** | Multi-factor signal composition (cell 가중치 학습) — **D16b 완료 후** | K.4 | 3h |
| **D18.7** | Peak capture / missed opportunity 집계 (D14.5 lag_kpi 재사용) | K.3 | 2h |
| **D19** | Phase 2.5 PHS skeleton (D8 trace + D11 cell + K.2 지표 integration) | plan_t12 + K.10 | 6-8h |
| **D19.5** | Fast-In/Out latency 최적화 (WS→order < 200ms) | K.5 | 4h |

### 단계 5: 안정화 관찰 — 48h+

| 순서 | 작업 | 예상 |
|---|---|---|
| **D20** | 봇 재시작 + 24h baseline 비교 | 대기 |
| **D21** | Phase 3 Winner amplification (pyramid / crisis amp / meta-learner) | T14 candidate |
| **D22** | KPI 검증 (asymmetry ≥ 1.5× 지속 확인) | 48h+ |

### 실행 원칙
- **D0~D5 완료 전 구현 금지** (토대 없이 Patchwork 방지)
- **단계 3 은 병렬 분담 가능** (Cross-cutting 과 Core 분리)
- 각 단계 끝에 봇 restart + 관측 (단일 큰 commit 금지)
- 매 commit 시 taxonomy 검증 통과 확인

## Part E — 제약 / 주의

- 모든 Plan v2 의 숫자는 `(seed)` / `(observed)` / `(example)` 태그
- 구현 시 신규 preg 키는 `_params_*` 모듈에 등록 → `_LIVE_CONFIG` override 허용 → learner 튠 대상 명시
- 단일 커밋 크기: 50 파일 이하 (큰 refactor 는 Phase 단위로 분리)
- 봇 restart 는 Phase 완료 단위로만 (세션 중간 restart 금지)

### E.1 — Per-Change Validation Gate (매 변경 북극성 정합 + 타당성 필수)

**원칙**: 모든 edit / commit / pset / preg 추가 **직전** 에 아래 3종 자가 검증 통과.

**A. 북극성 정합 체크 (필수 6개)**
1. Aggressive always profit — 수익 기회 축소 없음
2. Amplify-only (mult ≥ 1.0) — defensive dampen 없음
3. Flow invariant — 신호 차단/skip/reject 없음 (feedback_flow_not_block)
4. Asymmetry — winner 확대 / loser 축소 방향 유지
5. Data-driven — 모든 threshold preg + learner
6. No block filter architecture — gate/require/min_required 어휘 없음

**B. 타당성 체크 (필수 4개)**
1. 목적: 해결하려는 관측 증거 명시 (DB query / log)
2. 효과: 예상 개선 지표 (asymm / WR / latency / 기회 capture)
3. 부작용: 영향 받는 컴포넌트 + 악화 가능성
4. Rollback: 실패 시 원복 절차 (구체적 명령)

**C. Feedback 위반 체크 (memory)**
- `feedback_no_quick_patch_ever` — 🚨🚨 순간 패치/하드코딩/구조적 결함 절대 금지
- `feedback_no_hardcode_in_plans` — magic number 없음
- `feedback_flow_not_block` — 흐름 원칙
- `feedback_no_block_filter_architecture`
- `feedback_no_defensive_param_dampen`
- `feedback_code_verify_before_schema_spec` — 스키마 추정 없이 코드 확인
- `feedback_getattr_wiring_guard` — wire 검증
- 위반 시 즉시 재설계, 위반 없음 증명 후 진행

**D. 구조적 결함 자가 감지 (per-change must-pass)**
매 변경 전 아래 자가 질문 — 하나라도 "네" 면 재설계:
- [ ] 이 변경이 24h 후에도 의미 있는가? (곧 걷어낼 땜질이면 금지)
- [ ] 이 숫자/threshold 가 cell/preg 에서 튜닝 가능한가? (고정값이면 금지)
- [ ] 이 변경이 다른 모듈의 가정을 몰래 깨는가? (silent contract break 금지)
- [ ] 의도하지 않은 behavior change 가 있는가? (증거 기반으로 명시)
- [ ] Rollback 절차가 한 줄로 표현 가능한가? (불가능 = commit 금지)
- [ ] 근본 원인 증거 있는가? 아니면 증상만 가리는가? (feedback_root_cause 위반)
- [ ] "빨리 돌아가게만" 이 내심 동기인가? (품질 > 속도 원칙 위반)

**위반 시 강제 조치**:
- 해당 change 즉시 revert (commit 전 발견) 또는 revert commit (발견 후)
- memory 에 재발 방지 교훈 추가
- Jin 에게 보고 ("이 이유로 이 접근 폐기, 다른 설계로 재시작")

**검증 방법**:
- Self-review (Claude): 매 Edit/Write 전 속으로 3종 통과 확인
- Pre-commit hook lint (도입 시): 금지 어휘 + preg 등록 + taxonomy 대조
- Agent audit: 의심 시 `dev-audit-advisor` 또는 `harness-structure-advisor` 호출

**실패 시**:
- 작은 위반 → 재설계 후 재통과
- 큰 위반 → 해당 change 폐기 + 교훈 memory 추가

**중요**: 속도 vs 품질 상충 시 품질 우선. "일단 넣고 나중에 고침" 은 하드코딩 재발 유발 (T12 토큰 60% 낭비 교훈).

## Part F — End-to-End Pipeline 흐름 (북극성 축)

> **반드시 T13 Plan v2 에 포함**. Phase 를 나열만 하지 말고, 실제 거래 수명주기 각 stage 에 어떻게 끼어드는지 매핑.

### 5-Stage 파이프라인 × 데이터 흐름

```
┌─ STAGE 1. SELECTION ─────────────────────────────────────────┐
│  Ticker universe                                               │
│    → Signal providers (funding/ls_ratio/taker/FG/tech/macro)  │
│    → Signal scoring + normalize  [Phase 1.3 signal hygiene]   │
│    → quality_gate (blocks 분리)   [Phase 1.3]                 │
│    → cell_matrix routing          [기존 + composite 확장]      │
│    → top-N cohort selection       [Phase 3 winner 집중]        │
│    → Entry signal 확정                                         │
│  데이터: signals, ticker_baseline, cell_matrix, regime         │
│  북극성: exploration 희석 차단, 강자 집중                        │
└───────────────────────────────────────────────────────────────┘
               ▼
┌─ STAGE 2. EXECUTION ─────────────────────────────────────────┐
│  Size 계산                                                     │
│    → base_risk_pct × size_mult chain                          │
│        (session × regime × adaptive × cohort)                 │
│    → (exchange × group) composite 가중   [Phase 1]            │
│    → Liquidity layer cap                  [Phase 2]           │
│    → Crisis amp                           [Phase 3 regime]    │
│  Broker adapter (OKX/CAP/Alpaca)                              │
│    → Order placement + fill event                             │
│    → entry_params snapshot 저장                                │
│  데이터: trades (open), entry_params, regime at entry          │
│  북극성: crisis = max bet, scale via normalize                  │
└───────────────────────────────────────────────────────────────┘
               ▼
┌─ STAGE 3. MANAGEMENT (LIVE) ─────────────────────────────────┐
│  Tick-level position tracking                                  │
│    → live_exit_recalc (3-layer)          [기존 T11]           │
│    → Position Health Score (6-factor)    [Phase 2.5]          │
│        ├ price_score                                          │
│        ├ time_score                                           │
│        ├ signal_score (entry signal 재평가)                   │
│        ├ liquidity_score (분봉 spread/vol)                    │
│        ├ correlation (cohort drift)                           │
│        └ regime_score (flip since entry)                      │
│    → Pyramiding trigger (PHS high + winner)  [Phase 3]        │
│    → Cohort risk (corr / netting)            [Phase 3]        │
│    → Fast momentum cut (loser tail 차단)     [Phase 3]        │
│  데이터: positions_snapshots, position_health (신규)           │
│  북극성: winner amplify, loser fast cut                         │
└───────────────────────────────────────────────────────────────┘
               ▼
┌─ STAGE 4. EXIT ──────────────────────────────────────────────┐
│  Exit trigger 우선순위                                         │
│    → EMERGENCY (PHS ≤ preg_phs_tier_emergency)  [Phase 2.5]   │
│    → Signal reversal fast exit                   [Phase 3]    │
│    → TP / BEP / TRAIL / TIME / STOP (3-layer)    [T11 + 1]    │
│    → SIGNAL exit (DPM kill)                       [기존]       │
│    → broker_removed / orphan (cleanup)            [기존]       │
│  데이터: trades (close), exit_type, max_profit_pct, hold_sec   │
│  북극성: asymmetric — winner hold 길게, loser 즉시                │
└───────────────────────────────────────────────────────────────┘
               ▼
┌─ STAGE 5. POST-EVAL & EVOLUTION ─────────────────────────────┐
│  Hourly window (현행 6 learner):                               │
│    → session_mult / regime_mult (size)                        │
│    → max_hold (TIME 임계)          [composite 확장 Phase 1]   │
│    → profit_cap / trail_mult / bep_activate  [composite Phase 1] │
│  Event Bus (신규 Phase 1.5):                                   │
│    → learner Δ ≥ preg_audit_delta_pct → AI audit              │
│    → sustained_loss (ex×grp) → AI TUNE / PAUSE                │
│    → KPI asymmetry breach → AI consult                        │
│    → regime flip → 포지션 영향 평가                              │
│  Meta-learner (Phase 3):                                       │
│    → 각 learner 의 KPI 기여도 측정                              │
│    → learner weight 자동 조정                                   │
│    → Learner discovery (신규 composite key 생성)                │
│  Strategy Elo / Genetic (기존 + 강화 Phase 3):                 │
│    → 승자 전략 crossover                                        │
│    → 패자 전략 즉시 cull                                         │
│    → mutation rate regime-aware                                │
│  데이터: hour_stats, ai_event_audits (신규), strategy_performance │
│  북극성: self-improving, data-driven, no hardcode               │
└───────────────────────────────────────────────────────────────┘
               │
               └─▶ Feedback: preg update → Stage 1 재진입 (loop)
```

### Stage × Phase 매트릭스

| Stage | 기존 component | 신규 Phase | 시너지 포인트 |
|---|---|---|---|
| 1 Selection | signal providers, quality_gate, cell_matrix | 1.3 hygiene, 3 cohort | normalize 공유 |
| 2 Execution | size chain, broker | 1 composite, 2 liquidity, 3 crisis amp | composite key 확장 |
| 3 Management | live_exit_recalc, positions | 2.5 PHS, 3 pyramid/rotation | PHS 가 recalc 입력 |
| 4 Exit | 3-layer exit, DPM kill | 2.5 emergency tier, 3 fast exit | PHS tier 우선순위 |
| 5 Post-eval | 6 learner, Elo | 1.5 Event Bus, 3 meta-learner | learner 위 meta |

### 데이터 흐름 (SSOT 경유 여부)

| 데이터 | 저장소 | 읽기 | 쓰기 | normalize 경유? |
|---|---|---|---|---|
| signal score | `signals` | cell_matrix, gate | providers | Phase 1.3 |
| regime | in-memory + `group_regime_history` | size, exit | detector | — |
| position live | in-memory + `positions_snapshots` | PHS, recalc | tick loop | Phase 2.5 |
| trade result | `trades` | learner, meta | exit | quarantine filter |
| liquidity | `ticker_baseline` + 신규 column | size, PHS | Phase 2 job | Phase 2 |
| preg state | REGISTRY + `live_config.json` | 모든 stage | learner, Event Bus action | — |

### 북극성 각 Stage 적용 체크

| 원칙 | Stage 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Aggressive always profit | top-N | crisis amp | pyramid | winner hold | meta reward winner |
| Asymmetry | quality_gate (신중) | size asym | pyramid winner / cut loser | 우선순위 asym | meta-learner 비대칭 |
| No defensive dampen | mult ≥ 1.0 | mult ≥ 1.0 | mult ≥ 1.0 | — | — |
| Data-driven | normalize | preg chain | PHS learner | exit preg | meta + discovery |
| No hardcode | preg ref | preg ref | preg ref | preg ref | preg ref |

### T13 Plan v2 반드시 포함

- [ ] 5-stage 흐름도 (위 ASCII) 유지 + 새 hook 표시
- [ ] 각 Stage 에 현재 "북극성 위반 risk" 1줄 명시
- [ ] Stage × Phase 매트릭스 갱신 (Phase 추가 시)
- [ ] 데이터 흐름표 갱신 (신규 테이블 / column 추가 시)
- [ ] Feedback loop 명시 (Stage 5 → Stage 1 preg 반영 경로)

## Part G — 3-Tier Tick 아키텍처 (프로세스/데이터 분리)

**Why**: 현재 `invasion` 단일 프로세스가 {WS 수집 + Signal + 거래 + Learner + AI + Dashboard} 전부. PID rotation 빈번 (T12 관찰 88063→2736→2886→3985) = 한 부분 crash 시 데이터 수집까지 끊김. Multi-Matrix + Taxonomy 쌓아도 **데이터 결손** 이 있으면 학습 무의미.

### G.1 — 3-Tier 분리 설계

```
┌─ Tier 1 — DATA INGESTOR (독립, 내구성 최우선) ──────┐
│  Role: 외부 market 데이터 수집 → DB raw 저장만       │
│  Process: `invasion-ingest` (또는 async task 분리)    │
│  Inputs: WS (OKX/CAP/Alpaca), REST polling,          │
│         provider callbacks (funding/ls_ratio/...)    │
│  Outputs: DB 전용 테이블                              │
│    - market_ticks (신규)                              │
│    - market_candles_1m/5m/15m (정규화된 신규)         │
│    - provider_raw (신규, funding/ls_ratio/taker raw) │
│    - orderbook_snapshots (신규)                       │
│  Fail 시: Tier 2/3 무관하게 재시작, 데이터 결손 최소화│
│  Write 속도: 초당 수백~수천 row (bulk insert)         │
└──────────────────────────────────────────────────────┘
           ▼  (SQLite WAL read — Tier 2 병렬)
┌─ Tier 2 — TRADE ENGINE (빠름, 반응) ─────────────────┐
│  Role: DB raw 읽기 → feature/signal → order 실행     │
│  Process: `invasion-trade`                            │
│  Inputs: market_ticks, provider_raw, preg (cell mx)  │
│  Outputs:                                             │
│    - signals (현행)                                   │
│    - trades (현행, open/update/close)                 │
│    - positions_snapshots                              │
│    - feature_cache (신규, 계산된 indicator)           │
│  Fail 시: Tier 1 데이터는 계속 쌓임, 복구 후 이어감   │
│  Latency: tick → order < 200ms 목표                   │
└──────────────────────────────────────────────────────┘
           ▼  (DB history → 학습 window)
┌─ Tier 3 — LEARN/EVOLVE (주기적, 느림) ───────────────┐
│  Role: 완료 거래 + 시계열로 preg/strategy 튠          │
│  Process: `invasion-learn` (hourly cron 또는 tick)   │
│  Inputs: trades, signals, hour_stats, ticker_baseline│
│  Outputs:                                             │
│    - preg updates (cell_learn 경유)                   │
│    - strategy_performance (Elo)                       │
│    - ticker_baseline (normalize 재계산)               │
│    - ai_event_audits (Phase 1.5)                      │
│    - strategy_cell_matrix (Phase 2.5)                 │
│  Fail 시: Tier 2 는 기존 preg 로 계속 거래, 복구 후 재학습│
│  주기: hourly (learner), 30min (baseline), daily (Elo)│
└──────────────────────────────────────────────────────┘
```

### G.2 — DB 스키마 재설계 (Tier 경계)

| 테이블 | 현재 | Tier | 변경 |
|---|---|---|---|
| `market_ticks` | ❌ 없음 | **1 write / 2 read** | 신규, 초고빈도 append-only |
| `market_candles_*` | candles json 파일 | **1 write / 2,3 read** | DB 테이블로 이관 |
| `provider_raw` | 없음 (in-mem) | **1 write / 2,3 read** | 신규 append-only |
| `orderbook_snapshots` | 없음 | **1 write / 2 read** | 신규 (선택, 용량 큼) |
| `signals` | 있음 | **2 write / 3 read** | 유지 |
| `trades` | 있음 | **2 write / 3 read** | 유지 |
| `positions_snapshots` | 있음 | **2 write / 3 read** | 유지 |
| `feature_cache` | 없음 | **2 write self** | 신규 (indicator 캐시) |
| `ticker_baseline` | 있음 | **3 write / 2 read** | 유지, Tier 3 만 write |
| `hour_stats` | 있음 | **3 write / 2 read** | 유지 |
| `strategy_performance` | 있음 | **3 write / 2 read** | 유지 |
| `strategy_cell_matrix` | 있음 | **3 write / 2 read** | 스키마 확장 (Part B.2) |
| `ai_event_audits` | 없음 | **3 write** | 신규 (Phase 1.5) |
| `param_registry` (live_config) | json | **3 write / 2 read** | 그대로 (이미 tier 경계 맞음) |

**규칙**:
- **Write 권한 상호 배타** (한 테이블 = 한 Tier 만 write)
- Read 는 자유 (WAL 병렬)
- **Tier 경계 위반** = schema linter 차단

### G.3 — Data Flow Contract

```
Tier 1:  market raw → DB                 [append-only, no delete, no update]
Tier 2:  DB raw → feature → signal → order → DB trade  [write 최소]
Tier 3:  DB history → aggregate → preg delta → DB update   [hourly batch]
```

**금기**:
- Tier 2 가 market_ticks 를 update (→ Tier 1 의 일관성 깨짐)
- Tier 1 이 signals/trades 를 읽음 (→ 책임 경계 이탈)
- Tier 3 이 trade 를 close (→ latency 폭탄)

### G.4 — Process / IPC 옵션

**옵션 A: async task 분리 (1 프로세스, 3 event loop)**
- 구현 간단, 같은 프로세스 내 async task 로 tier 분리
- 단점: 한 프로세스 crash 시 전체 끊김 (현재 문제 해결 X)

**옵션 B: Multi-process (권장)**
- `invasion-ingest` / `invasion-trade` / `invasion-learn` 독립 프로세스
- IPC: SQLite WAL (read 병렬) + 필요 시 UNIX socket 신호
- 재시작 독립 (ingest crash → trade 는 기존 데이터로 계속, 복구 후 이어감)
- Supervisor: `systemd` / `start.sh` 에서 3 child
- SQLite 동시 write = 1 writer per table (tier 경계가 이 제약 해결)

**옵션 C: Queue 기반 (over-engineered 지금은)**
- Redis/NATS 같은 메시지 브로커
- SQLite 로 충분한데 복잡도 증가 → T13 scope 이외

**권장**: 옵션 B. 현재 SQLite WAL + 프로세스 분리로 충분. T14 이후 부하 증가 시 옵션 C 재검토.

### G.5 — Resilience 효과 (현재 vs 신구조)

| 시나리오 | 현재 모놀리식 | 3-Tier 분리 |
|---|---|---|
| WS 연결 끊김 | 전체 멈춤 | Tier 1 만 재연결, Tier 2 는 DB 최근 데이터로 계속 |
| Learner 에러 | 전체 crash → PID rotation | Tier 3 만 skip, Tier 1/2 정상 |
| AI API 장애 | Timeout 으로 block | Tier 3 의 AI audit 만 skip, 거래 무영향 |
| Signal 버그 | trade process crash | Tier 2 만, 복구 시 DB 최신 시점 pickup |
| DB corruption | 전체 | 각 tier 재시작 + 백업 복구 |

**T12 관찰**: PID rotation (88063→2736→2886→3985) = 현재 모놀리식의 crash-restart 사이클. **Tier 1 분리 시 데이터 연속성 확보** — 학습에 가장 중요.

### G.6 — Migration 단계 (T13 + T14)

| Step | 작업 | 예상 | Tier 영향 |
|---|---|---|---|
| G1 | DB 스키마 추가 (market_ticks / candles / provider_raw) | 2-3h | — |
| G2 | 기존 candles/json → DB 이관 script | 2h | — |
| G3 | Tier 1 분리 — ingest module → 독립 실행 가능 | 4-5h | Tier 1 |
| G4 | start.sh 3-child supervisor | 1h | 모두 |
| G5 | 모놀리식 → 3-process 전환 + 안정화 | 1-2일 | 운영 |
| G6 | Feature cache + Tier 2 최적화 | 3-4h | Tier 2 |
| G7 | Tier 3 hourly cron + AI Event Bus 분리 | 3-4h | Tier 3 |

**순서**: G1~G2 (스키마 준비, 무영향) → G3~G4 (Tier 1 분리, 내구성 즉효) → G5 (안정화 관찰) → G6~G7 (점진 완성).

### G.7 — 북극성 정합

- **Data-driven**: 데이터 결손 = 학습 실패. Tier 1 내구성이 북극성 토대.
- **Aggressive always profit**: Tier 2 가 learner/AI 블로킹 없이 빠르게 거래 → 기회 놓치지 않음.
- **Realtime**: Tier 2 latency < 200ms 목표 (learner 와 분리되어야 가능).
- **No hardcode**: Tier 간 경계는 DB 스키마 상 강제 (write 권한) — 프로세스 구조도 data-driven.
- **Self-improving**: Tier 3 만 느려도 Tier 2 는 계속 현재 preg 로 동작 → 학습은 진화, 거래는 상시.

### G.8 — 현재 DB 설계 평가 (답)

**Q (Jin)**: "우리 데이터베이스가 그런식으로 설계가 되어있나?"

**A**:
- ✅ SQLite WAL = **기술적으로 3-tier 가능** (read 병렬, write 직렬)
- ❌ 현재 스키마는 **tier 경계 없음** (write 권한 혼재, raw 테이블 부재)
- ❌ 프로세스 구조는 모놀리식 (단일 fail-point)
- ❌ Raw market data 저장 없음 (candles 는 json, tick 은 in-memory 휘발)
- → **T13/T14 에 G.6 Migration 필요**. 특히 G1 (스키마) + G3 (Tier 1 분리) 이 가장 임팩트 큼.

### G.9 — Plan v2 체크리스트 (Part C 확장)

- [ ] **3-Tier 스키마** — Tier 경계 명시 + write 권한 테이블별 선언
- [ ] **market_ticks + provider_raw + market_candles_* DB 테이블** 신규
- [ ] **Tier 1 독립 프로세스** (옵션 B) — start.sh 3-child supervisor
- [ ] **Data flow contract** 준수 증명 (lint / schema rule)
- [ ] **Resilience 검증** — 각 Tier 독립 restart 테스트

## Part H — Cross-Cutting Concerns (아직 구조적으로 비어있음)

T13 에서 **B / E / A 3개 필수 포함**. 나머지는 T14+.

### H.1 — Observability (Trace + Replay) 🔴 T13 필수
- **현재**: 분산 로그, 특정 거래 수명주기 추적 불가
- **필요**:
  - `trace_id` — signal → entry → tick → exit 관통 UUID
  - Structured event stream (event_type, trace_id, ts, payload)
  - Point-in-time replay (특정 시점 상태 재현 for debug/audit)
- **저장**: `trade_events` 테이블 (trace_id, ts, event_type, payload_json)
- **북극성**: "왜?" 답 없으면 학습/AI audit 증거 무효

### H.2 — State Reconciliation (broker ⇄ DB) 🔴 T13 필수
- **현재**: `broker_removed` 98건 = 이미 불일치 발생
- **필요**:
  - 주기적 broker positions vs DB trades 대조 job
  - Source of truth 규칙 (broker 우선, DB 는 기록용)
  - Order cancellation 실패 감지 + fallback
  - Unexpected position 감지 → Jin alert + auto-close (preg 정책)
- **주기**: preg_reconcile_interval_sec
- **북극성**: 돈 직접 위험 — 최우선

### H.3 — Auto Data Quality Monitor 🟡 T13 권장
- **현재**: Jin 지시 수동 75건 quarantine
- **필요**: 실시간 anomaly detector
  - ABS(pnl_pct) > contract.range_hard → `quarantined_anomaly` 자동
  - hold_seconds 극단치, TEST pattern, split event detection
  - Corporate action 캘린더 연동 (provider API)
- **wire**: Taxonomy runtime validator (Part B.3) 의 확장

### H.4 — Regression Safety (Canary + KPI Guard) 🔴 T13 필수
- **현재**: preg/코드 변경 즉시 전체 적용
- **필요**:
  - **Canary**: 새 preg 를 포지션 preg_canary_pct 또는 1-2 strategy 에만 우선
  - N시간 후 KPI diff 비교 → 악화 시 자동 revert
  - Event Bus (Phase 1.5) 와 통합
- **북극성**: Phase 실행마다 전체 위험 = 실행 장벽, canary 가 해결

### H.5 — Kill Switch + Emergency Protocol 🔴 T13 필수
- **현재**: `circuit_breaker_pct: -100` 사실상 disable, `consecutive_loss_halt: 20` 만 (trade-count)
- **필요**:
  - **Jin kill switch**: `touch data/KILL` 파일 감지 → 즉시 전체 close + halt
  - Drawdown circuit (시간 × 금액 × 비율 복합, 전부 preg)
  - Flash crash detector (분봉 vol 급증 + 단방향 폭주)
  - Escalation: console → MD log → Jin notify (Phase 1.5 Event Bus 재사용)
- **북극성**: Black swan 단일 손실 = 북극성 전체 무효화. Safety net 필수.

### H.6 — Capital Management Invariant 🟡 T14
- **현재**: 포지션 size 개별 계산, 총 노출 invariant 검증 불확실
- **필요**:
  - **Σ size ≤ 자본 × leverage_cap** 상시 유지
  - Margin utilization 실시간 모니터
  - Broker margin call 사전 대응
- **wire**: Tier 2 trade engine 의 order submit 전 pre-check

### H.7 — Performance Budget / DB Growth 🟡 T14
- **현재**: DB indefinitely growing
- **필요**:
  - Retention policy: raw tick = preg 일, aggregates = preg 일
  - Candles 286MB → DB 이관 후 더 커짐 대비
  - Query performance: partition / archive to cold storage
  - Index 계획: (metric, depth), (exchange, group, ticker), (ts DESC)

### H.8 — AI Model Safety 🟡 T13 일부
- **현재**: AI proactive_exit disable (T11 이미 처리)
- **추가 필요**:
  - AI ↔ learner 충돌 resolution rule (preg 우선도)
  - AI hallucination 감지 (response schema 검증, range check)
  - Model version lock (Gemini 업데이트 시 자동 revalidate)
  - AI budget hard-stop (runaway cost preg 한도)
- **wire**: Phase 1.5 Event Bus 의 입출력 규격화

### H.9 — Time Synchronization 🟡 T14
- **필요**:
  - Multi-exchange clock drift 감지
  - Candle 경계 vs order ts 정합 검증
  - UTC ↔ AEST 변환 일관성 (Jin feedback 과거 존재)
- **wire**: Taxonomy 에 `ts_unit`, `timezone` 필드

### H.10 — Failure Recovery / DB Backup 🔴 T13 필수
- **현재**: backup / PITR 불명
- **필요**:
  - 주기적 DB snapshot (preg_backup_interval_sec)
  - Point-in-time recovery 절차 문서화
  - Replay-able event log (H.1 trace 와 연계)
  - Bot complete loss 시 복구 runbook
- **북극성**: 데이터 loss = 학습 0-ize

### H.11 — Human-in-the-Loop Notification 🟡 T14
- **현재**: Jin 직접 로그 열람
- **필요**:
  - Critical event → push/SMS escalation
  - Jin 부재 중 며칠 돌아가도 안전
  - Kill switch 포함 (H.5)

### T13 필수 (H 에서)
**🔴 Critical**: H.1 (Observability trace), H.2 (Reconciliation), H.4 (Canary), H.5 (Kill Switch), H.10 (Backup)

**🟡 Recommended**: H.3 (Auto Data QA), H.8 (AI Safety 일부)

**🔵 T14 이후**: H.6 (Capital), H.7 (DB Growth), H.9 (Time Sync), H.11 (HIL Notify)

## Part J — Flow Amplifier (Jin T12 재정정, 🔴 T13 필수)

> **북극성 재확인**: 틀어막지 말고 **흐르게**. 차단/skip/제한 모두 금지. 모든 신호는 진입. Amplifier 는 "확장 여력 산정" 뿐 "통과 조건" 아님.



**Jin 정정**: "마이크로 배율 확장해서 amplify 하면 된다. 차단 아니라 확대가 답. 신호/거래량/유동성만 잘 잡으면 micro 가 오히려 amplify 에 유리."

**나의 이전 오류**: Entry EV "차단" gate 로 접근 = `feedback_no_block_filter_architecture` + `feedback_no_defensive_param_dampen` 정면 위반. 북극성 "amplify-only" 역행.

**올바른 방향**: Micro EV 가 절대값으로 작으면 **size 배율 자동 확장** 으로 절대 EV 를 목표 수준까지 끌어올림. 확장 제약은 유동성/신호/slippage — 전부 amplify-preserving.

### J.1 — 근본 철학 (북극성 = 흐름)
- **절대 원칙**: 신호 오면 진입. Skip 없음. 축소 없음. 막힘 없음.
- **Amplifier = 흐름 확대 장치**, 제한기 아님
- Micro 가 특히 amplify 에 유리 — %-변동성 큼, 절대 return size × 배율로 폭발
- "제약" 이라는 말 자체가 block-thinking — **"가용 확장 여력 측정"** 으로 재진술
- 유동성/slippage 는 **진입을 막는 벽 아니라 최대 흐름 폭 산정값**

### J.2 — Amplifier 규격

**공식 (entry 직전 trade engine 에서 size 재산출)**:
```python
def amplify_size(base_size_usd, signal, ticker, exchange, dims):
    # 1. 기대 절대 EV 계산
    exp_pct    = cell_resolve("expected_winner_pnl_pct", *dims)
    win_prob   = cell_resolve("win_probability",         *dims)
    los_pct    = cell_resolve("expected_loser_pnl_pct",  *dims)
    exp_net_pct = win_prob * exp_pct + (1-win_prob) * los_pct

    current_ev = base_size_usd * exp_net_pct / 100

    # 2. 목표 절대 EV (cell 학습)
    target_ev = cell_resolve("target_abs_ev_usd", *dims)

    # 3. 원하는 amplify 배율 (절대 EV 목표 기준)
    amp_desired = max(1.0, target_ev / max(current_ev, epsilon))

    # 4. 가용 확장 여력 측정 (3축, 전부 cell_learned 동적값)
    #    "상한 차단" 아님 — 시장 현실에서 흐를 수 있는 최대 폭
    amp_flow_by_signal = cell_resolve("flow_by_signal_strength", *dims) * signal.strength
    amp_flow_by_liq    = live_liquidity_capacity(ticker, exchange) / base_size_usd
    amp_flow_by_slip   = _flow_envelope_by_slippage(ticker, exchange,
                                                    cell_resolve("slippage_flow_budget_bps", *dims))

    # 5. 실제 amplify — desired 와 가용 flow 의 조화
    #    desired 가 flow 보다 크면 flow 에서 최대 흐름, 작으면 desired 그대로
    #    어떤 경우에도 ≥ 1.0 (base 이상 흐름)
    amp_final = max(1.0, min(amp_desired, amp_flow_by_signal, amp_flow_by_liq, amp_flow_by_slip))
    return base_size_usd * amp_final
```

**핵심 (흐름 철학)**:
- `amp_desired` = 목표 절대 EV 달성 배율 (amplify 의도)
- `amp_flow_*` = 시장이 자연스럽게 흡수 가능한 최대 흐름 폭 (저항 아니라 운하)
- `amp_final ≥ 1.0` **절대 하한** — 어떤 경우에도 축소 없음
- **Skip 없음 / reject 없음 / block 없음** — 모든 신호 진입
- 흐름 폭이 좁으면 그만큼, 넓으면 desired 까지. 물은 흐른다.

### J.3 — 유동성 흐름 폭 (Phase 2 Liquidity 재료)
```python
def live_liquidity_capacity(ticker, exchange) -> float:
    adv_usd         = cell_resolve("adv_usd_1h_avg", ticker, exchange)
    util_pct        = cell_resolve("liquidity_util_pct", ticker, exchange)
    spread_modulator= cell_resolve("spread_flow_modulator", ticker, exchange)
    return adv_usd * util_pct * spread_modulator
```
- ADV × util = **시장이 자연스럽게 흡수하는 흐름 폭** (차단 아님)
- Spread 증가 시 흐름 폭 자연 조정 (막는 게 아니라 물길이 좁아지는 것)
- 하한은 base_size (항상 진입 보장)

### J.4 — Slippage 흐름 envelope
```python
def _flow_envelope_by_slippage(ticker, exchange, budget_bps):
    # realized_slippage_bps 회귀 모델로 "자연스러운 흐름" 영역 산정
    # budget 을 벗어나는 size 는 "흐름 폭 바깥" — 차단 아니라 envelope 끝
    model = cell_resolve("slippage_model_params", ticker, exchange)
    return _envelope_max_size_factor(model, budget_bps)
```
- 기존 `realized_slippage_bps` (trades 테이블) 로 모델 학습
- Budget 은 cell_learned — 시장 상황 따라 자동 확장/축소 (하지만 항상 base ≥ 1.0)

### J.5 — Signal Strength 기반 흐름 확장
```python
flow = cell_resolve("flow_by_signal_strength", *dims) * normalize(signal.strength, ticker, exchange)
```
- 신호 강할수록 흐름 폭 자연 확대 — **conviction × size** = 북극성 amplify
- 약신호도 base ≥ 1.0 로 흐름 유지 (학습 기회 지속)
- "약하니까 막는다" 는 북극성 위반 — 약해도 흐르게 두고 학습

### J.6 — Cold Start 처리
- Cell sample 부족 → fallback chain (Part B.2)
- 모든 cell 이 비어도 `global_seed_target_abs_ev_usd` (preg) 사용
- **Seed 도 북극성 amplify-only — 1.0 이상만**

### J.7 — KPI 신규 추적
- **Avg trade absolute EV** (24h) — 평균 기대 수익 $ — 목표 ≥ cell_resolve target
- **Amplify distribution** — 1.0× / 1-2× / 2-5× / 5×+ 비율
  - 1.0× 100% = amplifier 비활성 (버그 의심)
  - 5×+ 과도 (50%+) = 제약 미작동 or 극단 신호 집중
- **Liquidity utilization** — base_size / live_capacity 평균 (과잉 체결 방지)
- **Realized vs expected slippage diff** — 모델 정확도

### J.8 — Wire 위치
- **Stage 2 Execution 초입**: size 계산 chain 의 마지막 layer
- 기존 `base_risk_pct × session × regime × adaptive` 체인 뒤에 **amplifier layer 추가**
- 3-Tier 의 Tier 2 trade engine 내 inline
- Phase 2 Liquidity 완료 후 full 작동 (분봉 data 필요)

### J.9 — 구현 단계 (Part D 에 삽입)
- **D18.5** (Phase 2 Liquidity 직후): Amplifier core + 제약 3축 wire
- **D19** PHS 와 통합 (PHS 높은 포지션 추가 amplify 허용)
- **D20** 관찰 — Avg trade abs EV 추이 + amplify distribution

### J.10 — 북극성 정합 (흐름 원칙)
- **Flow invariant**: 모든 신호 진입 보장 (skip/reject/block 절대 금지)
- **Amplify-only**: amp_final ≥ 1.0 강제 하한
- **흐름 확장**: 제약은 차단이 아니라 "자연스럽게 흘러갈 수 있는 폭" 산정
- **No block filter**: filter/gate/check_pass 어휘 전부 제거 — "flow_envelope" 사용
- **Data-driven**: 흐름 폭 세 변수 전부 cell_learned 동적
- **Asymmetry**: winner 쪽 흐름 확대 + loser 는 exit 구조로 자연 정리 = 비대칭 폭발

### J.10.5 — 금지 어휘 (북극성 준수 검증 시 lint)
`차단 / block / reject / skip / disallow / gate_pass / min_size_required / require / block_filter`
→ **구현 코드 / Plan 문서 / 커밋 메시지** 전부 금지. 검출 시 해당 로직 재설계.

### J.11 — Unfillable-but-Queued 상태 (Codex: 북극성 flow vs 물리 현실)

**문제**: H.2 broker 우선 SSOT + H.5 kill switch + market closed + broker reject + 잔고 부족 = 물리적으로 체결 불가능. "모든 신호 진입 / 축소 없음" 은 이 경계에서 깨짐.

**해법 (차단 아닌 지연/변형)**: 신호는 **queued** 로 살리고 실행만 지연/재시도/변형. 구조적으로 "reject" 로 해석하지 않음.

**상태 정의 (신규 enum)**:
| 상태 | 의미 | 처리 |
|---|---|---|
| `queued_market_closed` | 거래소/티커 세션 닫힘 | 다음 open 에 실행 (expire preg 내) |
| `queued_insufficient_margin` | 증거금 부족 | 자본 회복 시 재시도 or size 확장 포기 후 base size 실행 |
| `queued_broker_reject_transient` | 브로커 일시 오류 (rate-limit 등) | 지수 백오프 재시도 (preg) |
| `queued_liquidity_wait` | 유동성 envelope < base_size | 분봉 orderbook 회복 대기 (preg window) |
| `halted_kill_switch` | Jin kill 명시 | 인간 개입만 해제 (자동 재개 없음) |

**저장**: `signal_queue` 테이블 (signal_id, state, enqueued_ts, expire_ts, retry_count, trace_id, resolution)

**Resolution**: 실행 성공 시 `trades` 로 연결, expire 시 `expired` 상태 (여전히 기록 유지 — 차단 아니라 시간 초과).

**북극성 정합**:
- 신호 소멸 아님 (**queued 상태로 유지 = 흐름 지속**)
- 실행만 상황 회복 후 resume
- 단일 예외 `halted_kill_switch` 는 Jin 명시 의사 (인간 권한, 물리적 금지 아니라 의식적 중단)

**Lag KPI 추적**: `signal emit ts` → `execution ts` 간격 집계 (D14.5 lag_kpi_hourly 에 포함).

### J.11 — 즉시(D-1) 임시 조치 (T13 MVP 전)
- `adaptive_sizing_max_mult` / `adaptive_sizing_min_mult` 현재값 확인
- 북극성 위반 시 (min < 1.0 이면) 즉시 1.0 으로 수정
- Amplifier 본 구현 전까지 최소한 축소 방지

---

## Part K — Opportunity Detection & Sensitivity Expansion (북극성 핵심)

> **북극성 기본 베이스**: 딱 찾아서 확 잡아서 먹고 빠짐. Sensitivity ↑ / Measurement 정밀 / Fast in·out. 막는 건 없음.

**목적**: 현재 시스템의 signal source 를 확장해 더 작은 기회도 감지 + 타이밍 정밀화 + 조기 진입/조기 exit edge 확보.

### K.1 — 현재 보유 지표 (baseline)
- funding / ls_ratio / taker / fear_greed / volatility / price_action / macro_regime / precomputed / technical / momentum (provider)
- ATR / ADX (technical)
- Session / regime / cell_matrix score
- normalize() 5-metric (atr / size / signal / volume / pnl_std)

### K.2 — 신규 지표 후보 (T13 도입 우선순위순)

**🔥 Tier 1 — 즉시 도입 (분봉/현재 데이터로 계산 가능, 개발 최소)**
| Indicator | 계산 | Why 북극성 |
|---|---|---|
| **Bid/Ask Imbalance** | orderbook snapshot: (bid_vol - ask_vol) / total | Micro-structure 선행지표, 진입 타이밍 정밀 |
| **Microprice** | (bid·ask_size + ask·bid_size) / total | true price 추정, fair value divergence 감지 |
| **Trade Aggressor Ratio** | taker_buy / taker_sell (기존 taker provider 확장) | 진짜 방향 감지 (quote 와 다름) |
| **Volume Spike z-score** | vol_1m / stdev(vol_30m) | Opportunity ignition 감지 |
| **Spread Percentile** | live_spread / 30d_spread_dist | 유동성 이벤트 감지 |
| **Realized Vol (5m)** | stdev(log returns 5m) | 실시간 변동성 (ATR 보다 반응 빠름) |
| **Candle Body Ratio** | \|close-open\|/(high-low) | trend strength 실시간 |
| **Intrabar Rejection** | wick/body ratio | Reversal 초기 감지 |
| **Volume Profile POC** | 시간당 최대거래량 가격대 | Magnet / S·R level |

**🟡 Tier 2 — 중기 도입 (외부 data source 필요, 2-3일 개발)**
| Indicator | 소스 | Why |
|---|---|---|
| **OI Change Rate** | OKX/exchange API | Position build-up 방향 |
| **Funding Rate Velocity** | 기존 funding delta | 심리 변곡점 |
| **Basis (Spot-Futures)** | 다중 exchange | Arbitrage 기회 + 방향 선행 |
| **Exchange Flow (crypto)** | on-chain / exchange API | Whale 입출금 = 대형 이벤트 precursor |
| **Stablecoin Supply Change** | on-chain | Buying power 변화 |
| **Cross-exchange Price Diff** | OKX vs Binance | Lead-lag 감지 |
| **Volatility Term Structure** | 다양한 window vol 비교 | Vol regime shift |
| **Pair Correlation Shift** | rolling window | Regime change leading |

**🔵 Tier 3 — 장기 도입 (advanced, T14+)**
- Options IV / Put-Call Ratio (where available)
- Kyle Lambda (price impact coefficient) 추정
- News event detection (NLP)
- Social sentiment flow
- Iceberg order detection
- TWAP/VWAP deviation

### K.3 — Sensitivity 측정 강화

**현재 이슈**:
- quality_gate 가 WR% 만 봄, **기회 놓친 횟수 (missed opportunity)** 측정 없음
- Exit 가 max_profit 피크 근처 아니라 peak 의 42% 에서 털림 (T12 관측)

**추가 지표 (측정 강화)**:
| KPI | 계산 | 목표 |
|---|---|---|
| **Signal→Price lag** | signal emit ts vs 가격 반응 ts | 짧을수록 edge |
| **Missed opportunity rate** | 진입 안 한 ticker 중 N분 후 +X% 이상 움직인 비율 | cell 로 학습 |
| **Peak capture ratio** | realized_pnl / theoretical_max | > 70% 목표 |
| **Edge decay** | 진입 후 time vs PnL decay curve | exit 타이밍 학습 |
| **Time-to-profit** | entry → first hit +0.X% | 짧을수록 fast-in-out |
| **Time-to-stop** | entry → loss threshold | loser 빠른 감지 |

### K.4 — Multi-Factor Signal Composition

**지금**: signal score = provider 들의 가중 합 (가중치 고정)

**진화**:
```python
signal_score = Σ (weight_factor[dim] × normalized_factor_value)
  where weight_factor = cell_resolve("signal_factor_weight", factor_name, *dims)
```
- 각 factor 의 가중치를 **cell 이 (ex×grp×ticker×regime) 별 독립 학습**
- factor 기여도 = 실제 그 신호로 진입한 거래의 realized EV
- 북극성 fit 한 factor 가중치 확대, 부적합 factor 축소 (0 으로 가진 않음 — 학습 기회 유지)

### K.5 — Fast-In / Fast-Out 구조

**Fast-In** (조기 진입):
- 신호 emit → order placement latency < preg_entry_latency_budget_ms (목표 < 200ms)
- Tier 1 data ingestor 가 WS tick 즉시 DB 넣음
- Tier 2 가 DB polling 아닌 SQLite WAL notify / file watch
- 또는 in-memory signal bus (tier 2 within-process)

**Fast-Out** (조기 exit):
- PHS 급락 → 즉시 exit (Phase 2.5)
- Peak capture ratio 실시간 계산 → 피크 탐지 시 TRAIL 가속
- Momentum reversal tick-level 감지 → SIGNAL exit

### K.6 — 구현 단계 (Part D 에 삽입)

| Step | 작업 | Tier | 예상 |
|---|---|---|---|
| **D10.5** | DB 스키마 확장 (orderbook_snapshots, trade_events) | 1 | G1 과 병합 |
| **D14.5** | Tier 1 신규 indicator 계산 job (분봉 orderbook → DB) | K.2 Tier 1 | 3-4h |
| **D18.6** | Multi-factor signal composition (cell 가중치 학습) | K.4 | 3h |
| **D18.7** | Missed opportunity rate + Peak capture ratio 추적 | K.3 | 2h |
| **D19.5** | Fast-In/Out latency 최적화 (WS → order < 200ms) | K.5 | 4h |
| **T14** | Tier 2 외부 source 지표 (OI/funding velocity/basis) | K.2 Tier 2 | 2-3일 |

### K.7 — 북극성 정합
- **Sensitivity**: 더 많은 factor = 더 작은 기회 감지
- **측정 정밀**: peak capture / missed opportunity 로 학습 피드백
- **Fast in/out**: latency 최소화 + live PHS 로 조기 진입/exit
- **No blocking**: 새 indicator 들도 "차단 조건" 아니라 "흐름 강화 입력"
- **Cell-learned weight**: Factor 기여도 자동 튠 = data-driven 진화
- **Asymmetric reward**: 기회 포착 = winner 흐름 확대 촉매, loser 는 PHS+exit 가 자연 정리

### K.9 — Exchange-Specific Indicator Semantics (Part B.1 와 통합)

**문제**: 같은 지표명이어도 exchange 마다 의미/scale/cadence 완전 다름.

| 지표 | OKX (crypto) | CAP (fx/idx/com) | Alpaca (stock/etf) |
|---|---|---|---|
| Bid/Ask Imbalance | orderbook level 20 | 중개 quote 1-3 level | NBBO 만 |
| Microprice | 의미 있음 | spread 커서 의미 작음 | 정상 |
| Aggressor Ratio | taker buy/sell 공식 tag | 미제공 (역산) | trade condition code |
| Volume Spike | base asset 기준 | USD notional (제공 단위) | shares (USD 환산 필요) |
| Realized Vol | 분당 tick 많음 → 1m 정확 | 분당 tick 드뭄 → 5m 기본 | regular hours 만 |
| Tick cadence | 초당 수천 | 분당 수~수십 | 초당 수백 |
| Orderbook depth | 깊음 (top 20+) | 얕음 (top 3-5) | NBBO (1 level) |
| Spread unit | bps | pips/points 혼재 | cents/bps |

**결론**: K.2 의 모든 신규 지표는 **(ticker, exchange) pair-keyed** 로 taxonomy / normalize / cell 등록 (Part B.1 + B.3 준수).

**구현 계약**:
- 각 지표마다 contract card 에 `{exchange}_variants` 필드 — exchange 별 계산식 / cadence / unit 명시
- Taxonomy yaml 구조:
  ```yaml
  metric: bid_ask_imbalance
  per_exchange:
    okx:    {depth_levels: 20, cadence_sec: 1,  unit: ratio}
    cap:    {depth_levels: 3,  cadence_sec: 60, unit: ratio}
    alpaca: {depth_levels: 1,  cadence_sec: 5,  unit: ratio}
  ```
- Consumer 는 `get_indicator(metric, ticker, exchange)` — exchange 해석 자동

### K.10 — Position Live Monitoring (실시간 감시 = "먹고 빠짐" 필수 조건)

**지금**: Tier 2 가 주기적 polling 으로 포지션 업데이트. Peak 탐지 늦음.

**필요**: 포지션 open 후 **모든 지표를 tick 단위 재평가** → PHS 실시간 + peak 감지 + fast exit

**구조 (Part H.1 trace + Phase 2.5 PHS + K.2 지표 통합)**:
```
Position Open
  → trace_id 발급 (H.1)
  → live_monitor_loop(position, trace_id):
       매 tick (exchange cadence 기반):
         1. K.2 지표 전부 재계산 (exchange-specific)
         2. PHS 6-factor 재산출 (2.5)
              - 이 중 signal_score / liquidity_score 는 K.2 값 재사용
              - 즉 PHS factor = K.2 지표의 composition
         3. Peak detection (max_profit_pct 갱신 + momentum decay 측정)
         4. Exit 3-layer recalc (T11 live_exit_recalc + K.3 peak capture 반영)
         5. 이벤트 기록 (trace_id, position_health 테이블)
         6. Fast-exit trigger 판정 (PHS 급락 / peak reversal / signal flip)
```

**Tick cadence (exchange-aware)**:
- OKX: WS tick 마다 (초당 수십 회)
- CAP: quote 수신 마다 (분당 수 회)
- Alpaca: trade print 마다 (초당 수 회)

각 exchange 의 **native cadence** 로 loop 돌림 → 과다 polling 없음, 누락 없음.

**저장 (H.1 trace + 2.5 PHS 통합)**:
```
position_health (
  trace_id, position_id, ts,
  phs_score, phs_factors_json,
  indicator_snapshot_json,  # K.2 전체
  exit_params_live_json,    # 3-layer 재계산 결과
  peak_pnl_pct, peak_ts,
  state,                    # healthy/degrading/reversing/exiting
  PRIMARY KEY (position_id, ts)
)
```

**Fast-Exit 트리거 (Part J 흐름 원칙 유지 — skip 아님, exit 타이밍 앞당김)**:
- PHS 급락 (Δ > cell_resolve 임계) → exit 앞당김
- Peak reversal 감지 (pnl/peak ratio < cell_resolve 임계) → TRAIL 가속
- Signal 반전 (진입 신호 factor 다수 negative) → SIGNAL exit 조기
- 단 모든 트리거 **exit 속도만 조정**, 진입은 이미 끝난 상태 — 흐름 원칙 유효

### K.11 — 통합 다이어그램 (K + J + H.1 + 2.5)

```
Signal emit (Stage 1)
  ├─ K.2 모든 지표 (ticker, exchange) 실시간
  ├─ Signal composition (K.4, cell 가중치)
  └─ Flow Amplifier (Part J) → optimal size

Order (Stage 2)
  └─ trace_id 발급 (H.1)

Position Live (Stage 3, K.10 loop)
  ├─ 매 tick: K.2 지표 재평가
  ├─ PHS (2.5) = f(K.2 지표 composition)
  ├─ Peak detection + edge decay (K.3)
  ├─ Exit 3-layer recalc (T11)
  └─ Fast-Exit trigger (PHS / peak / signal)

Exit (Stage 4, fast-out)
  └─ trace_id 종료 (H.1)

Post-eval (Stage 5)
  ├─ Cell learn (B.2): K.2 factor 가중치 + flow envelope + peak capture
  └─ Meta-learn: 어떤 지표 조합이 실제 먹었나
```

### K.8 — Trade Philosophy 요약 (북극성 기본 베이스 재확인)
```
기회 감지 (sensitivity ↑)
   → 딱 찾아서 (measurement 정밀)
   → 확 잡아서 (flow amplify, size 확장)
   → 먹고 (fast capture, peak 근처 exit)
   → 빠짐 (loser 즉시 정리)
```
막는 것 없음. 모든 축은 흐름을 키우고 시기를 맞추는 데 쓴다.

---

## Part I — Harness / Agent Pool / Skill Tree 사전 점검 (T13 Step -1)

**언제**: T13 세션 로드 → Quick-Start 끝난 직후 → D0 (Taxonomy) 시작 전.

**목적**: Plan 을 실제 실행할 수 있는 Harness 인프라 (agent + skill + hook) 가 준비되어 있는지 audit. Gap 있으면 D0 전에 먼저 보완.

### I.1 — Harness 구조 점검 (선행 3호출)

```
1. harness-structure-advisor 호출
   → Agent pool 정합 / canonical_files drift / north_star 정합 / 규정 준수

2. harness-drift-detector 호출
   → canonical path / docs-code 일치 / memory feedback 위반 / 60줄 상한

3. dev-audit-advisor 호출
   → 코드 품질 / dead code / wire 정합 / commit 위생
```

### I.2 — Agent Pool vs T13 필요 매핑

| T13 작업 영역 | 기존 agent | Gap / 필요한 보강 |
|---|---|---|
| **Taxonomy v1 (D0)** | dev-audit-advisor, dev-wire-guardian | Missing: **metric-taxonomy-auditor** (yaml contract 런타임 검증 전담) |
| **Audit (D1)** | dev-audit-advisor | 기존 adequate, 단 unit tag 확장 작업 지시 필요 |
| **Unit fix (D2)** | dev-coder | adequate |
| **Plan v2 (D4)** | Plan, debate | debate 로 교차검증 권장 (아키텍처 규모) |
| **Kill Switch (D6)** | — | Missing: **safety-circuit-auditor** (Kill/Circuit/Canary 전담) |
| **Backup (D7)** | ops-executor | adequate |
| **Trace (D8)** | dev-coder + ops-log-quality-auditor | Gap: **trace-id 런타임 검증 agent** (event chain 무결성) |
| **Reconcile (D9)** | — | Missing: **reconciliation-watcher** (broker ⇄ DB 주기 비교) |
| **DB 스키마 (D10)** | dev-coder, harness-drift-detector | adequate (schema 변경 시 drift 체크) |
| **Cell API (D11)** | dev-coder, dev-refactor-advisor, ops-param-tuner | Gap: **cell-matrix-specialist** (CellKey / cell_resolve QA) |
| **Canary (D12)** | — | Missing: **safety-circuit-auditor** (D6 과 공유) |
| **Event Bus (D13)** | alert-triage skill + dev-coder | adequate |
| **Signal hygiene (D14)** | — | Missing: **signal-pipeline-auditor** (quality_gate / dedup / normalize) |
| **Data QA (D15)** | ops-log-quality-auditor | adequate (확장 작업) |
| **Cell 이관 (D16)** | dev-coder, dev-refactor-advisor | adequate |
| **Tier 1 분리 (D17)** | dev-coder, harness-structure-advisor | Gap: **tier-separation-specialist** (프로세스 경계 / IPC contract) |
| **Liquidity (D18)** | dev-coder | adequate |
| **PHS (D19)** | — | Missing: **position-health-specialist** (PHS factor 계산 + tier action QA) |
| **Exit 전반** | dev-entry-gate-specialist (entry 전담) | Missing: **exit-specialist** (3-layer exit + PHS tier) |

### I.3 — 신규 필요 Agent Spec (Skeleton)

**다음 5개 T13 에서 가장 먼저 spec 작성 + `.claude/agents/` 에 추가**:

1. **metric-taxonomy-auditor**
   - Trigger: preg / metric 추가/변경 시 자동
   - 역할: `docs/metric_taxonomy.yaml` vs 코드 일치성 런타임 검증
   - 입력: 변경 diff + taxonomy yaml
   - 출력: MISSING / UNIT_MISMATCH / RANGE_VIOLATION 리포트

2. **safety-circuit-auditor**
   - Trigger: Kill Switch / Circuit Breaker / Canary 관련 코드 변경
   - 역할: Emergency exit path 검증, canary rollback 절차 검증
   - 출력: Safety 체크리스트 + 실패 시 block

3. **reconciliation-watcher**
   - Trigger: hourly (또는 broker 이벤트 급변 시)
   - 역할: broker positions vs DB trades 대조 → 불일치 감지
   - 출력: mismatch 리스트 + 자동 resolution 제안

4. **cell-matrix-specialist**
   - Trigger: Multi-Matrix Cell / CellKey / cell_resolve 변경 시
   - 역할: depth 일관성 / fallback chain 검증 / sparse cell 관리
   - 출력: cell tree 건강도 + promote/demote 제안

5. **signal-pipeline-auditor**
   - Trigger: signal / quality_gate / provider 변경 시
   - 역할: score scale 정규화 / dedup / 혼입 감지
   - 출력: 오염 source + fix 제안

**후순위 (T14+)**:
- tier-separation-specialist
- position-health-specialist
- exit-specialist (dev-entry-gate-specialist 와 짝)

### I.4 — Skill Tree (slash commands) 점검

**현재 활용**:
- `/harness-mode` — bootstrap ✓
- `/debate` — 아키텍처 교차검증 (Plan v2 승인 시) ✓
- `/alert-triage` — Event Bus 통합 ✓
- `/research` — 외부 자료 ✓
- `/backtest` — Phase 배포 전 검증 ✓
- `/review` — PR 검토 ✓
- `/security-review` — Kill switch 보안 영역 ✓

**Gap (필요 시 추가)**:
- `/kpi-check` — 북극성 KPI 자동 측정 (winner/loser asymm, WR per cell)
- `/cell-heatmap` — Multi-Matrix Cell 시각화 (metric × dim)
- `/phase-deploy` — 새 Phase 배포 체크리스트 실행 (canary → KPI 비교 → 승격)
- `/unit-audit` — Taxonomy 위반 스캔 (D1 audit 자동화)

### I.5 — Hook / settings.json 점검

**T13 도입 시 추가 필요**:
- Post-edit hook: Taxonomy 변경 시 자동 runtime validator 재로드
- Pre-commit hook: 매직넘버 lint (Part B.3 taxonomy 대조)
- Scheduled hook: reconciliation-watcher hourly 실행

**명령**: `/update-config` 스킬로 settings.json 수정.

### I.6 — T13 Step -1 실행 체크리스트 (Codex 반영: Plan v2 수용 기준 먼저)

- [ ] harness-structure-advisor → 구조 점검 리포트
- [ ] harness-drift-detector → drift 리포트
- [ ] dev-audit-advisor → 코드 품질 기저선
- [ ] **Plan v2 수용 기준 고정** — B.3 Taxonomy / G 3-Tier / H.2 Reconcile / K.3 lag KPI 의 acceptance criteria 확정 (이게 선행)
- [ ] Skill gap 4개 필요성 확정 (I.4)
- [ ] settings.json hook 필요분 목록 (I.5)
- [ ] 결과 요약 → `tasks/harness_audit_t13.md` 생성
- [ ] Jin 승인 후 D0 (Taxonomy) 착수
- [ ] **Agent spec 5개 skeleton** (I.3) 은 Plan v2 승인 후 → 실제 필요한 agent 범위 확정된 후 작성

### I.7 — 원칙
- **Agent 없으면 작업 불안정** → Gap 채우기 전 해당 D 단계 착수 금지
- **Skill 없으면 수작업 반복** → 반복 5회+ 예상 시 slash command 화
- **Hook 없으면 휴먼 실수** → 자동화 가능한 검증은 전부 hook

## Part L — (축소됨, M 에 흡수) Signal 기본 인프라 선행 공사

> **Jin 지적 (2026-04-22)**: "M (Dynamic) 있으면 L 할 필요 있나?"
> **답**: L 의 개별 preg / quality_gate threshold 마이그는 **M 이면 불필요** (provider set 자체가 동적이라 per-provider threshold 의미 없음). L 은 **M 의 선행 뼈대** 작업만 남김.

### L 축소 — M 과 중첩 없는 선행 항목만 유지
- ✅ **signal_blocks 테이블 분리** (결함 5: drop 데이터 소실 → 태그 이관) — M 의 shadow mode 기록에도 필요
- ✅ **Provider score per-exchange normalize (taxonomy)** (결함 6) — M 의 attribution 에도 필요
- ✅ **Tier 1 tick-level dedup** (결함 7 변형) — signal level 이 아닌 **raw tick coalescing** 으로 이동, 어차피 G1 에 포함
- ❌ **quality_gate threshold 개별 preg 마이그 (L.4 원안)** — 폐기. M 의 active_provider_set / meta-signal 이 대체.
- ❌ **결함 4 (global threshold)** — M 이면 threshold 개념 자체 소멸, patch 불필요

### 결함 매핑 재조정
| # | 결함 | 이전 계획 (L) | 수정 계획 (M 기반) |
|---|---|---|---|
| 4 | Global threshold | L.4 preg 마이그 | **M.1 active_provider_set 으로 대체 (preg 불필요)** |
| 5 | Drop = 데이터 소실 | D14.1 signal_blocks | D14.1 유지 (M shadow 에도 필요) |
| 6 | Provider score scale | L.2 taxonomy | B.3 taxonomy 내 per_exchange.provider_score variants (유지) |
| 7 | OKX coalescing | L.2 signal dedup | G1 **Tier 1 tick-level dedup** 으로 이관 (signal 이전 단계) |

### L 원문 내용은 "레퍼런스" 섹션으로 아래 유지 (버리지 않음, 과거 설계 기록용)

---

## Part L — Signal Metrics Exchange-Redesign (T12 관측 기반 신설, 원문 레퍼런스)

**Jin 관측 (2026-04-22 01:45)**: "CAP OKX signal drop 율 저게 맞아? 시그널도 exchange 별 metrics 재설계 필요?"

### L.1 — 측정된 구조적 결함 (1h window)
| ex | total | acted% | drop% | avg_score | avg_strength |
|---|---|---|---|---|---|
| alpaca | 2056 | 29.6% | 70.4% | -0.6 | 10.58 |
| cap | 1048 | 30.3% | 69.7% | **-16.7** | 18.0 |
| okx | 12122 | **10.9%** | **89.1%** | -6.4 | 8.95 |

**Drop 주요 이유**: quality_gate:low_wr > neutral_direction > agreement 미달

### L.2 — 구조적 결함 4종 (결함 4~7, 결함 1~3 은 snapshot_t12 참조)

**결함 4: Drop threshold global 적용 (exchange 특성 무시)**
- OKX tick 10× 밀도 → 같은 임계 compound drop
- CAP score bias (-16.7 baseline) → global gate 부적합
- **해법**: quality_gate / neutral / agreement threshold 전부 `cell_resolve(..., ex, grp)` 경유

**결함 5: Drop = 데이터 삭제 (북극성 flow 위반)**
- 현재: quality_gate 거부 → signals 테이블에 점수 -48.9 로 흔적만 남고 학습 자료 X
- 북극성: **drop 대신 tag 후 signal_blocks 로 이관**, learner 는 "왜 drop 됐나" 학습
- **해법**: D14 `signal_blocks` 분리 → drop 이유 cell-learned 임계로 자동 진화

**결함 6: Provider score scale 미정규화**
- CAP avg -16.7, Alpaca -0.6 — 같은 숫자 전혀 다른 의미
- **해법**: Part B.3 Taxonomy 에 provider_score per_exchange scale contract 필수
- `normalize(provider, ticker, exchange, raw_score)` zscore 반환

**결함 7: OKX tick density signal coalescing 부재**
- 같은 (ticker, direction) 초당 수십 signal 중복 emit
- **해법**: Phase 1.3 dedup 에 **exchange-aware coalescing window** 추가
- `dedup_window_sec = cell_resolve(ticker, exchange)` — OKX 짧게, CAP 길게 자동

### L.3 — Redesign 원칙 (북극성 준수)

| 원칙 | 적용 |
|---|---|
| Flow (막지 말고 흐르게) | drop 삭제 금지 → signal_blocks 로 이관, 학습 자료 유지 |
| Amplify-only | quality_gate 통과 signal 의 size 확장 (Part J Flow Amplifier) |
| Data-driven | 모든 threshold (`low_wr_threshold`, `neutral_strength_min`, `agreement_min_pct`) cell_learned |
| Scale-independent | provider score per-exchange normalize (B.3 taxonomy) |
| Exchange-aware | dedup window / quality threshold / agreement 요건 exchange 별 독립 |

### L.4 — 필요 신규 preg (전부 cell-keyed, seed 는 observed baseline)

- `signal_quality_wr_threshold` (ex×grp) — current global value 를 exchange 별 분리
- `signal_neutral_strength_min` (ex×grp) — neutral direction 판단 임계
- `signal_agreement_min_pct` (ex×grp) — provider 간 동의율
- `signal_dedup_window_sec` (ex×ticker) — coalescing 창
- `provider_score_baseline_mean` (ex×provider) — normalize 기준
- `provider_score_baseline_stdev` (ex×provider) — zscore 분모

### L.5 — Signal Flow 파이프라인 (재설계)

```
Provider raw emit (Tier 1, market data)
  → per-exchange coalescing (dedup window, cell-learned)
  → per-provider normalize (zscore, ex 별)
  → weighted composition (cell-learned weights, Part K.4)
  → quality_gate (cell-learned threshold, drop 대신 태그)
  → signal_blocks OR active_signals (분기만, 삭제 X)
  → Flow Amplifier (Part J) — size 확장
  → Order (Tier 2)
```

모든 단계의 threshold 가 cell_learned. Drop 이라는 개념 자체 제거 → tag 로 통일.

### L.6 — D14 확장 (Part D 에 삽입)

기존 D14 "Phase 1.3 Signal hygiene" 를 아래로 확장:
- D14.1 `signal_blocks` 스키마 분리 (drop → tag 이관)
- D14.2 Provider score per-exchange normalize (taxonomy)
- D14.3 Exchange-aware coalescing window
- D14.4 Quality/neutral/agreement threshold cell-keyed preg 마이그
- D14.5 Lag KPI (기존)

### L.7 — 결함 1~7 통합 요약 (T13 fix 대상)

| # | 결함 | 출처 | T13 해결 |
|---|---|---|---|
| 1 | `max_profit_pct=0` DB flush 누락 | anomaly_snapshot | K.10 Position Live Monitor |
| 2 | TIME→TRAIL_PROTECTED 무한 suppression | anomaly_snapshot | exit_cycle 재설계 + 2.5 PHS |
| 3 | alpaca asset_group=crypto 분류 오류 | anomaly_snapshot | Phase 1.3 group 정규화 |
| 4 | Drop threshold global (ex 무관) | Part L | L.4 preg 확장 + cell |
| 5 | Drop = 데이터 삭제 (flow 위반) | Part L | D14.1 signal_blocks 이관 |
| 6 | Provider score scale 미정규화 | Part L | B.3 taxonomy provider_score |
| 7 | OKX signal coalescing 부재 | Part L | D14.3 dedup window |

---

## Part M — Dynamic Signal Generation (북극성 완전 진화)

**Jin 지적**: "Signal 자체를 이렇게 픽스시켜놓는게 맞나? 다이나믹한 signal 생성 방법?"

### M.1 — 현재 구조 (반쯤 정적)
- Provider 10개 **고정**: funding / ls_ratio / taker / fear_greed / volatility / price_action / macro_regime / precomputed / technical / momentum
- 각 provider 의 **계산 수식도 하드코딩** (aggregator 고정)
- Learner 는 provider **가중치 만** 조정 (Part K.4)
- **Provider set / 결합 방식 / 조건부 활성** = 고정

### M.2 — 동적 Signal Generation 접근 (5층)

**Layer 1: Provider Composition 동적**
- 어떤 provider 조합이 어느 상황에 best 인지 cell 학습
- `active_provider_set = cell_resolve("best_provider_subset", ex, grp, ticker, regime, session)`
- 강한 provider 만 활성 — 약한 provider 는 비활성 (dormant, lifecycle 동일)

**Layer 2: Regime-Conditional Activation**
- Crisis regime: funding + fear_greed + macro 우선
- Risk_on: momentum + volatility + technical
- Specific session: session-dominant provider set 자동
- 전부 cell_learned 스위치 (0~1 연속 값, 완전 off 아님 — 흐름 유지)

**Layer 3: Function Composition Evolution (Genetic)**
- 기존 provider 들의 **수식 조합을 mutation 으로 진화**
- 예: `new_signal = f(funding) × g(ls_ratio) + h(taker)` 에서 f, g, h 가 evolvable operator
- Elo 토너먼트로 성능 우수 composition 생존
- strategy_evolution 과 유사하지만 signal level

**Layer 4: Meta-Signal (성과 기반 실시간 재조정)**
- 최근 N 거래의 realized PnL 을 signal 단위로 attribute
- Credit assignment: "이 signal 이 있어서 이 pnl" 측정
- Attribution 결과 → 실시간 weight 업데이트 (hourly 보다 빠른 online)

**Layer 5: AI-Generated Meta-Signal**
- Gemini: "현재 market context + provider outputs + recent performance → 새로운 판단 축 제안"
- 구조: AI 가 **feature combination 제안** → cell 학습 → 성과 있으면 정식 provider 승격
- Budget 내 (Phase 1.5 Event Bus 재사용)

### M.3 — Evolutionary Signal Discovery (M.3.1~M.3.3)

**M.3.1 — Feature Engineering 자동화**
- Raw market data (tick / orderbook / minute candle) 에서 **자동 feature 추출**
- 후보 feature 풀 (ex 별):
  - statistical: rolling mean / std / skew / kurtosis on various windows
  - cross-asset: correlation drift, basis, lead-lag
  - microstructure: imbalance, microprice deviation, aggressor ratio
  - regime-sensitive: conditional volatility, jump detection
- Genetic selection: 성과 우수 feature 생존

**M.3.2 — Signal Template Mutation**
- Template: `if condition then side + strength`
- condition / threshold / strength formula 전부 evolvable
- 예: "funding > threshold1 AND volume_spike > threshold2 then long strength=min(v1, v2)"
- Mutation: threshold 변경 / operator 변경 / 새 조건 추가

**M.3.3 — Cross-Exchange Signal Discovery**
- 예: OKX basis vs CAP index → divergence signal (직접 거래 아닌 방향 예측)
- Cross-exchange lead-lag 자동 발견
- 한 exchange 의 tick → 다른 exchange 의 signal 생성

### M.4 — Dynamic Signal Lifecycle

| 상태 | 전환 조건 | 설명 |
|---|---|---|
| **candidate** | genetic/AI 제안 | shadow mode (거래 안 함, 성과 기록만) |
| **trial** | shadow 에서 positive EV | 작은 size 로 live test |
| **active** | trial 성공 | 일반 signal 로 승격 |
| **amplified** | 지속 우수 성과 | 가중치 확대, Flow Amplifier 우선 |
| **dormant** | 성과 저하 | 실행 중단, 관측 계속 |
| **retired** | 장기 dormant + 대체 있음 | 풀에서 제거 |

**전환 임계 전부 preg/cell-learned** — 하드코딩 금지.

### M.5 — 북극성 정합

- **Data-driven**: 새 signal 생성 자체가 data 기반
- **Flow**: candidate 도 shadow 로 살림, retired 도 기록 유지 (삭제 아님)
- **Amplify-only**: 성과 우수 signal 확대 (dormant 는 off 아님, weight 축소)
- **Exploration dilutes winners 균형**: M.1 active set 이 top-N 만 선택 — 과도 탐색 차단
- **Self-improving**: signal 자체가 진화 (parameter 학습 뿐 아니라 structure 학습)
- **Exchange-aware**: provider set / discovery 모두 exchange 별 독립

### M.6 — 구현 단계 (T13 후반 + T14 대형)

**T13 범위 (MVP)**:
- M1 Provider active set cell_resolve (L1) — 기존 provider subset 동적 선택 (2h)
- M2 Regime-conditional weight (L2) — regime 별 provider weight multiplier (2h)
- M3 Meta-signal attribution (L4) — credit assignment 기본 구조 (4h)
- Dynamic signal lifecycle 테이블 스키마 (1h)

**T14 이후**:
- M4 Genetic function composition (L3) — signal evolution engine
- M5 AI-generated meta-signal (L5) — Gemini 제안 파이프라인
- M6 Feature engineering auto (M.3.1) — raw data → feature pool
- M7 Cross-exchange discovery (M.3.3) — 다중 exchange lead-lag

### M.7 — 즉시 가능한 간이 진화 (T13 내)

T13 MVP 단계에서 가장 임팩트 큰 동적성 도입:
- **Top-K active provider** (per ex×grp×regime) — 약한 provider 비활성 → 신호 노이즈 감소
- **Regime switch**: regime 이 바뀌면 provider weight map 자동 스왑
- **Realized EV attribution**: 최근 거래의 pnl 을 신호 단위로 역산 → weight 업데이트

이 3가지만 T13 에 들어가도 signal 이 **상황 따라 자동 변하는** 구조로 전환.

### M.8 — 위험 / 안전 장치

- **Shadow mode 필수**: 새 signal 은 최소 N 거래 shadow 확보 전까지 실거래 가중치 0
- **Over-fit 방지**: cell sample 임계 (Part B.2), 분리 배포 (E.1 D16.5)
- **Flow 유지**: 어떤 signal 도 완전 off 없음, weight 최소 floor preg
- **AI budget 제한**: Layer 5 는 Event Bus budget 내에서만

---

## Part O — Loss Forensic + Full-Pipeline Auto-Correction Loop (Jin T12 지적)

**Jin 관찰 기반 지적**: "Loss 는 최적 타임 청산 + loss 난 이유 기반 전략 자동 수정 + loss 유발 signal 까지 재정의."

결함 10 (신규): 실시간 추적 부재로 batch-exit 발생. Timer-based TIME 이 동일 cohort 일괄 청산 유발. **Loss 원인 역추적 + 자동 수정 loop 부재**.

### O.1 — 현재 구조 한계 (관측)
- Loss 발생 → DB 기록 → learner 가 aggregate 로 hourly 튠
- **개별 loss 원인 분해 없음** (어느 factor 가 기여?)
- Signal / size / timing / regime / correlation 귀인 불가
- Loss 유발 signal pattern 이 그대로 살아서 재발

### O.2 — 5-Stage Loss Forensic Pipeline (관측 시점 → 수정 시점)

```
Stage L0: 실시간 최적 exit (timer 폐지, PHS 기반)
  ← Phase 2.5 PHS + K.5 Fast-Out
  ← tick-level 독립 판단, cohort timer 없음

Stage L1: Loss 확정 후 원인 분해 (causal attribution)
  entry_signal_attribution:
    - 어느 provider / factor 가 주도?
    - Score 크기 vs 실제 결과 gap
  entry_condition_attribution:
    - regime / session / cohort 가 적절?
    - cell_matrix score 실측 정합
  size_attribution:
    - Flow Amplifier 판단 적절?
    - size / risk_budget 비율
  hold_attribution:
    - PHS 어느 factor 가 degradation 놓침?
    - peak 도달 후 반환 속도
  exit_attribution:
    - 실제 exit timing vs optimal timing (peak reversal 시점)
    - optimal = retrospective max profit 위치

Stage L2: Attribution 저장 (신규 테이블 loss_attribution)
  (trade_id, factor, contribution_score, evidence_json, ts)

Stage L3: Factor-specific cell 가중치 업데이트
  signal provider weight: 잘못된 factor ↓ (amplify-only 유지, floor > 0)
  size scaling: 해당 cell 의 size_mult 조정
  PHS factor weight: degradation 놓친 factor 우선순위 ↑
  regime / session mult: 틀린 상황 judgment 에 패널티 (minimum > 1.0 유지)

Stage L4: Signal Redefinition (M.4 lifecycle 연동)
  패턴 재발하는 loss-유발 signal composition:
    → amplified weight 감소 (완전 off 아님 — 학습 유지)
    → trial 로 demote 가능
    → 대체 pattern 생성 (genetic mutation / AI meta-signal)
  Strategy level:
    → Elo 토너먼트에서 loss 기여 strategy mutation 우선
    → 새 hybrid 자동 생성
```

### O.3 — Optimal Exit Timing (retrospective)

각 trade 마다 hold 중 max_profit_pct 와 실제 exit_pct 차이 측정:
```
optimal_exit_pct = max_profit_pct
realized_exit_pct = pnl_pct
exit_lag_score = (optimal - realized) / max(optimal, epsilon)
```
- exit_lag_score 높을수록 "더 잘 먹을 수 있었다"
- Cell 별 평균 exit_lag_score → PHS fast-out threshold 역진화
- 모든 임계 cell_resolve (하드코딩 금지)

### O.4 — Loss Attribution 계산 (factor 별)

각 loss 에 대해:
```python
def attribute_loss(trade):
    factors = {}

    # Signal: 진입 시 어느 provider 가 얼마나 주도?
    factors["signal_provider"] = signal_contribution_breakdown(trade.entry_params)

    # Entry condition: regime/session/ticker 적합도
    factors["entry_context"] = cell_resolve_match_score(trade.entry_dims)

    # Size: Flow Amplifier 결정이 실제 size 적절했나?
    factors["size"] = size_outcome_analysis(trade.size_usd, trade.max_profit_pct)

    # Hold: PHS factor 중 어디서 degradation 놓쳤나?
    factors["phs_factor"] = phs_attribution_over_hold(trade.position_health_ts)

    # Exit: timer vs optimal gap
    factors["exit_timing"] = retrospective_optimal_vs_realized(trade)

    return factors  # normalized contribution weights
```

### O.5 — 자동 수정 연결 (전 pipeline 영향)

각 factor 의 contribution 값이 cell 업데이트 규칙 (전부 preg) 에 따라 반영:
- Signal provider attribution 누적 → M.4 weight update
- Entry context attribution → cell_matrix score normalize factor 조정
- Size attribution → Flow envelope 공식 파라미터 (cell_learned)
- PHS factor attribution → PHS factor weight (Phase 2.5 learner)
- Exit timing attribution → PHS tier threshold 재조정

**북극성 정합**:
- 모든 down 방향 조정은 **amplify 축소** 아닌 **상대 우선순위 재조정** (floor 존재)
- Signal / strategy retire 는 구조적 퇴장 아닌 shadow/dormant (lifecycle)
- "이유 있는 loss" 는 학습 자료, 삭제 X (flow)

### O.6 — 신규 preg 후보 (전부 cell-keyed)
- `loss_attribution_enabled` (global 기본 on)
- `attribution_min_sample` (신뢰 임계)
- `phs_factor_weight_*` (cell 별 PHS factor 가중치)
- `exit_lag_score_threshold_*` (fast-out trigger)
- `signal_provider_penalty_floor` (최소 weight 하한)

### O.7 — Storage 스키마 (T13 추가)

```
loss_attribution (
  trade_id TEXT,
  attribution_ts REAL,
  factor_signal_provider JSON,
  factor_entry_context JSON,
  factor_size JSON,
  factor_phs JSON,
  factor_exit_timing JSON,
  total_contribution REAL,
  applied_learners JSON,  -- 어느 learner 에 반영됐나
  PRIMARY KEY (trade_id)
)
```

### O.8 — 구현 단계 (Part D 에 삽입)

- **D10.6** loss_attribution 테이블 (G1 스키마 확장)
- **D19.5** optimal exit timing 계산 모듈 (PHS 와 함께)
- **D19.6** Loss attribution engine (Stage L1 계산)
- **D19.7** Factor-specific cell update (Stage L3)
- **D19.8** Signal redefinition feedback (Stage L4, M.4 연동)

### O.9 — 북극성 정합 재확인
- **Flow**: loss signal 자체 삭제 X (shadow/dormant 로 흐름 유지)
- **Amplify-only**: 수정은 상대 가중치 조정, 절대 축소 X
- **Data-driven**: attribution 규칙 전부 cell_learned
- **Self-improving**: 매 loss 가 pipeline 전체 개선 입력
- **Asymmetry**: winner 는 증폭 그대로, loser 는 원인 해체 후 재조립

### O.10 — 결함 매핑 갱신

| # | 결함 | T13 해결 |
|---|---|---|
| 10 | Batch-exit (실시간 추적 부재, timer 의존) | L0 PHS + K.5 Fast-Out + Timer 폐지/subordinate |
| (전체 loss) | 원인 분해 부재 | O.1~O.8 Forensic + Auto-Correction Loop |

## 참조
- 현행 plan: `tasks/plan_t12_exchange_group_liquidity.md`
- 메모리: `feedback_no_hardcode_in_plans`, `feedback_adaptive_learner_attack`, `feedback_aggressive_always_profit`
- T12 commits: `f6679d95` / `46cd9c6a` / `0f8ddd5e` / `d322feef` / `3ba9fcc0` / `88514bc2`
- 관찰 로그: `tasks/observation_log_t12.md`
