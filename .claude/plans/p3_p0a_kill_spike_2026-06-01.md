# P3 P0a — KILL-Spike (offline config-variant pass-rate) — 2026-06-01

Parent SSOT = `.claude/plans/p3_self_evolve_2026-06-01.md` (REFRAME "증명 먼저"). Decision gate for whether P2 generator is worth building.

## Question (binary)
기존 7~10 전략의 **bounded numeric config 변종**이 기존 게이트(real-fee)를 **held-out 데이터**에서 통과하는 비율이 ~0 인가 >0 인가?
- **IS~0** → 피처공간 고갈(강한 증거, 피처 병목) → 생성기 보류, feature/fee(P0b/P1)로 redirect.
- **IS>0 · OOS~0** → 과적합/미일반화 → 역시 feature/validation 병목.
- **IS>0 · OOS>0** → 진짜 탐색공간 존재 → 생성기(P2) 정당.

## 코드 현실 (Understand 워크플로우 검증)
- `ReplayEngine.__init__(config)` 전략 하드코딩(`all_strategies()`, engine.py:80) — `strategies=` seam 추가.
- 전략 파라미터 = 모듈상수 inline. **싸게 변이 가능 = threshold/gain/TTL/strength**만. window 상수는 MarketView precompute 고정(rsi_14/donchian_40/ma_200) → 변이 시 인디케이터 재계산 필요 = **P0a 제외**.
- Gate: `passed = relative ∧ risk_adjusted ∧ statistical`(gate.py:246), Tier3 DSR≥0.95 ∧ LCB>0, 단일 trial DSR=PSR. `is_oos_spread` 미게이팅.
- alt-data → 전략 0 도달(검증). MarketView=고정 18 TA. → 변이는 동일 피처공간 재탐색.

## Build (각: TDD → fresh-Claude 적대리뷰 → behavior-gate → 커밋, real-fee)
1. **seam** — `ReplayConfig.strategies: tuple[BaseStrategy,...] | None = None`; ReplayEngine None→`all_strategies()`. **기본 None=라이브 경로 byte-identical**(behavior-0).
2. **variants** — 변이 가능 파라미터만 모듈상수→클래스 attr(`self.X`, default=기존값) 최소 리팩터 + 전략별 `PARAM_BOUNDS`(precompute-lock 검증 후 varyable만) + 변이 생성기(subclass attr override, 그리드 3pt/param, 총량 cap, variant_id 태그).
3. **registry** — greenfield `data/p0a_registry.sqlite` 테이블 `variant_trials`(PK cell_key+variant_run_id): trial_count·passed·pnl_r_mean·lcb·ucb·is/oos·created_ts. 재시작 영속·누적 trial = DSR search-breadth 입력.
4. **evaluator** — walk-forward IS/OOS 분할(`walk_forward_splits`, embargo=warmup+max_ttl) → IS 통과 + OOS 통과 **둘 다** 측정 + **DSR trials = 총 변이수**(다중검정 보정) + per-cell 집계.
5. **driver** — `polaris/scripts/run_p0a_spike.py`: 그리드 로드→변이별 replay(IS/OOS)→evaluate_gate→registry append→집계(overall/per-cell/per-strategy IS·OOS pass-rate)→**VERDICT**(피처병목 vs 탐색공간). 구조화 출력.

## Mandate
오프라인 전용(라이브 트레이딩/sizing/T4 무접촉) · seam 기본값=byte-identical · 새 dampen 0 · DEMO · 거부키워드 0 · 9-stack 무접촉 · 별도 DB(라이브 `polaris_live.sqlite`는 read-only bar source) · single-writer 존중 · dev=Claude.

## 정직성 안전장치
OOS held-out + DSR honest-N 보정 없으면 grid-search가 in-sample passer 양산 → 결정게이트 무의미. 둘 다 load-bearing. 데이터 부족(walk_forward 빈 튜플)이면 silent-shrink 금지·"data-bounded" 보고.

## 결과 (2026-06-01, build+adversarial review+fix+재리뷰 완료)
**verdict = `VALIDATION_STARVED`** (초기 build의 FEATURE_BOTTLENECK은 적대리뷰가 REJECT → 수정 후 정직 verdict).
- **🔴 결정적 발견**: 벤치마크 게이트 Tier-3(NIG LCB>0)가 **thin OOS N에서 구조적으로 통과 불가** — lookahead 오라클(psr=0.997)도 lcb<0로 FAIL, 강한 +0.2R/per-trade-Sharpe2 edge도 N≥69 필요한데 이 데이터 max leak-free OOS N=51. ⇒ **0-pass는 피처 고갈이 아니라 검증 기아**. positive control(강한 합성 edge가 실제 N에서 게이트 통과 가능?)이 verdict를 게이팅: 불가 시 VALIDATION_STARVED, FEATURE_BOTTLENECK 주장 차단.
- 수정: ttl_bars(replay precise-exit FSM 무효)+momentum_gain/gap_gain(sizing-only, 엔트리셋 불변) PARAM_BOUNDS 제거 → trials_searched=18(엔트리 변이만, no phantom). embargo purge 실제 강제(_partition_trades). warmup>IS윈도→data_bounded. honest_dsr 실제 게이팅+진짜 cross-variant 분산(2-pass). vacuous 테스트 7개 실질화 + "no-inert-knob" 가드 + 5 label 직접 테스트.
- 검증: full suite 1671 green, ruff/mypy clean, behavior-0(게이트 무수정·라이브경로 byte-identical), 거부키워드 0. 2 신선 재리뷰 = APPROVE/APPROVE_WITH_NITS.

## 함의 (다음 스텝)
**생성기(P2) 보류는 맞지만 "피처 병목 증명" 아님.** 진짜 병목 = **검증 스택이 현 데이터 N에서 edge를 판별 불가**(debate의 "검증 굶음" 확증). 순서 재확인: **데이터 누적**(라이브 봇이 거래 쌓아 OOS N≥~69 도달) → P0b(fee/churn+exit recompute) → P1(alt-data→MarketView 피처로 피처공간 실제 확장) 후 KILL-스파이크 재실행. 게이트 NIG prior가 per-trade-R 스케일에 과확산이란 별도 발견 = 게이트 재보정은 별도 결정(debate 대상, P0a 범위 밖).

## 남은 follow-up (low, 안전방향 nit — 비차단)
- positive control N = run 전역 max (per-cell 아님) → per-cell PC가 더 보수적(현재는 false VALIDATION_STARVED 생성 불가). 
- honest_dsr 변이 분산 = per-cell (run-wide 아님) → oos_pass가 verdict 게이팅 안 하므로 무해.
