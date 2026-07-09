---
type: research
status: spec-final
date: 2026-07-09
context: DEMO/PAPER virtual — 매우 활발화 wave, 3 병렬 빌드그룹
invariant: virtual_loosen(virtual, real) — real 인자 byte-identical / -1R rail·9-stack·사이징 곱셈체인 무접촉(빈도·슬롯만)
---

# Very-Active Wave — 빌드 스펙 (2026-07-09)

DEMO/PAPER 전용. 목표 = virtual 체결 수백+/일 (aggressive, flow_not_block).
현황: 167체결/13전략, net +$394. 병목 = 신호→진입 전환(dedup/쿨다운/슬롯).
3그룹 **파일 disjoint** → 병렬 머지-클린. base = 현 main(db-writer-split + reentry 0.5 커밋 포함, merge-base 신선화 의무).

**공통 불변식** — 모든 real 경로 byte-identical. `bar_seconds` 물리-바 프리미티브 무접촉(exit_params/loser_timeout/_production_recalc 공유). 사이징 곱셈체인·per-symbol/cluster 노셔널 캡·-1R 스탑 무접촉 — 본 wave 는 **빈도(바 케이던스)·슬롯(동시 카운트)·커버리지(focus)** 만 확대. 비퇴화(무의미 임계 금지): 데이터-게이트 유지.

---

## Group A — 타임프레임 하향 (virtual_loosen, 공급 확인분만)
- **branch**: `very-active/tf-downgrade`
- **파일 disjoint**: strategy 파일 8종만 (reentry.py / botctl.py 무접촉)
- **패턴**: `timeframe="X"` → `timeframe=virtual_loosen("<down>", "X")` (import-time; supertrend `dispatch_eligible=virtual_loosen(True,False)` 선례 동일). `from polaris.strategies._virtual_loosen import virtual_loosen` 추가.

| 파일:라인 | venue | real tf | virtual tf | 바공급 evidence |
|---|---|---|---|---|
| equity_rsi_bb_pullback.py:52 | alpaca | 1D | 1H | 1H 1.75M/4127sym |
| connors_rsi2.py:103 | alpaca | 1D | 1H | 상동 |
| supertrend.py:159 | okx | 1H | 15m | okx 15m 263k/227sym |
| ema_crossover.py:104 | okx | 1H | 15m | 상동 |
| macd_ema_trend_pullback.py:116 | okx | 1D | 1H | okx 1H 107k/228sym |
| cci_reversion.py:111 | capital | 1H | 15m | capital 15m 167k/168sym |
| fx_range_fade.py:73 | capital | 1H | 15m | 상동 |
| fx_breakout_basket.py:77 | capital | 1H | 15m | 상동 |

- **제외**: rsi_bb_pullback (okx 15m 이미 최하단, 5m 공급 0 → INERT 위험). 5m 전면 회피(okx/alpaca 5m 0건; capital 5m 有나 부하규율상 15m 1-step, capital 15m→5m 는 후속 스테이지).
- **빈도 효과**: 전부 1-step 하향 4x 바케이던스. alpaca 1D→1H(세션당 ~1→~6.5바)가 최대 증분 — 저빈도 8전략이 유의미 emit 진입. 실진입은 A와 독립적으로 쿨다운/슬롯 게이트.
- **부하**: 해당 심볼군 tick당 bar-row read 최대 4x (mapping 실측). db_writer 이관 후 여유. 8전략 동시라도 4x 상한(5m 회피).
- **⚠️ 리뷰 체크포인트**: tf 하향은 virtual 에서 `bar_seconds` 파생 horizon(loser_timeout·maturity gate·cooldown window)을 비례 축소 = **의도된 빠른 턴오버**. real byte-identical, -1R 스탑거리·사이징 무변. 등록≠발화: 머지 후 15m/1H replay smoke 로 실 emit 검증(feedback_verify_firing_after_build).
- **테스트**: (1) 각 전략 property — VIRTUAL=1 → metadata.timeframe == down, unset → real(byte-identical). (2) 통합 — market_view 가 down-tf 바로 빌드(공급 확인). (3) 발화 smoke.

---

## Group B — 쿨다운 0.25 + 슬롯 캡 virtual 확대 (reentry.py 단일)
- **branch**: `very-active/cooldown-slot-virtual`
- **파일 disjoint**: `polaris/core/isolation/reentry.py` 만 (env 활성화 불필요 — 기본값이 `_VIRTUAL`서 loosened; botctl 무접촉 → C와 겹침 0). core→strategies 레이어링 금지이므로 `virtual_loosen` import 금지, 기존 `_VIRTUAL` 플래그 재사용.
- **리스크-레일 불변 전제(명시)**: `tailored_concurrent_cap` 은 exact (venue,symbol,strategy_id) 동시 same-side **슬롯 수**만 — 사이징/per-symbol 노셔널 캡/cluster 캡/-1R 레일 **미접촉**. 슬롯 상향은 COUNT 상한일 뿐, 3번째 포지션도 독립 사이징 + downstream per-symbol/cluster 캡이 **총노출 여전히 바인딩**. 비퇴화: n≥CS3_N_THRESHOLD(20) & win_rate≥floor(0.55) 게이트 유지 → 프루븐 네임만 확대(uniform 아님).

**변경 1 — 쿨다운 factor env-tunable 0.5→0.25 (virtual only)**
- 현: `_COOLDOWN_FACTOR = 0.5 if _VIRTUAL else 1.0` (L106).
- 후: 신규 env `POLARIS_VIRTUAL_COOLDOWN_FACTOR` 기본 **0.25**, `_VIRTUAL` 분기에서만 적용. real=1.0 byte-identical. (`_env_float` 헬퍼가 L265 정의 → factor 계산을 헬퍼 정의 이후로 재배치하거나 인라인.)
- 효과: 오늘 `reason=reentry_cooldown` 스킵 **0건**(실측) → 금일 처리량 무변. 저비용·안전한 선제완화(향후 tf 하향으로 쿨다운 바인딩화 시 대비). 부하 ~0.

**변경 2 — tailored cap ceiling virtual 확대 2→3**
- 현: `TAILORED_CAP_CEILING = _env_int(_ENV_TAILORED_CAP_CEILING, 2)` (L277).
- 후: 기본값 `3 if _VIRTUAL else 2` (env override 유지). real 기본 2 byte-identical.
- 효과: **실 병목** — 오늘 `concurrent_same_side_open` 스킵 **676건**(weekend_funding_capitulation_maker 365 / session_breakout 157 / weekend_thin_book_flush_maker 116…). 프루븐 네임(20+거래 & win≥0.55)에 3번째 동시슬롯 → 최상위 병목 직격. 부하 ~0(SQL count 쿼리 수 불변).
- **테스트**: (1) 쿨다운 — real=bar_seconds byte-identical, virtual 기본 0.25, env override, `bar_seconds` 자체 무변(타 3 consumer byte-identical). (2) cap — real 기본 2 / virtual 기본 3, thin-sample(n<20) & 약엣지(win<0.55) 는 여전히 1. (3) 회귀 — cap=1 default caller byte-identical.

---

## Group C — OKX focus 시트 확대 (botctl env 단일, 점진 + 부하 가드)
- **branch**: `very-active/focus-expand`
- **파일 disjoint**: `tools/ops/botctl.py` `_spawn_env()` 만 (코드 무변 — knob 전부 기존 env-override). A/B와 겹침 0.
- **실 바인딩 진단**: OKX active/focus ~65 는 `POLARIS_WATCH_MAX`(기본 대형) 아닌 **OKX liquidity floor** 가 결정 — `passes_liquidity_floor` (`POLARIS_LIQFLOOR_OKX_MIN_VOL_24H_USD` 등, `LIQFLOOR_ENV_PREFIX` schema.py). 따라서 floor **점진 하향**이 focus 확대 레버, WATCH_MAX 는 상한 가드로 유지.
- **변경**: `_spawn_env()` 에 `env.setdefault(...)` 로 (a) `POLARIS_LIQFLOOR_OKX_MIN_VOL_24H_USD` 점진 하향값 (b) `POLARIS_WATCH_MAX` 상한 가드값. 숫자는 magic 아님 = 캘리브레이션 대상 → **스텝 1개씩**, 값은 빌더가 현 floor 로그에서 읽어 1-step(예: 현 floor의 하위 분위)만 낮춤. 셸 override 유지.
- **빈도 효과**: OKX 스캔 심볼 ~65 → 확대 → 더 많은 신호 소스. 중간 임팩트.
- **부하 (최고 위험)**: watch 확대 = REST bar fan-out + DB write + 이벤트루프 work 증가. **과거 focus 로테이션 STALL 이력** (feedback_single_heavy_workflow_cpu_freeze). db_writer 이관 후 여유 있으나 **점진 필수**: 1-step 하향 → 1 사이클 loop-tick 시간·DB write 볼륨 관측 → STALL 임계 미만 확인 후 다음 step. WATCH_MAX 상한 = 무한 팽창 하드가드.
- **테스트**: (1) `liquidity_floor_for_venue` env override 해석 단위테스트. (2) 부하 — floor 하향 전후 loop-tick 시간 측정, STALL 임계 미만 assert. (3) watch 집합 증가하되 WATCH_MAX 로 bounded assert.

---

## 병렬 실행 노트
- A(strategy 8) / B(reentry.py) / C(botctl.py) **파일 완전 disjoint** → 3 워크트리 병렬, 머지 충돌 0.
- 우선순위(임팩트): **B변경2(슬롯, 676 병목 직격) ≈ A(저빈도 8전략 각성) > C(focus, 부하위험) > B변경1(쿨다운, 금일 0효과·선제)**.
- 각 그룹 builder ≠ reviewer: fresh Claude sub-agent 리뷰 의무. sub-agent prompt = DEMO 명시 + aggressive bias + 거부키워드 sweep + length cap + vault r·w.
- 동시 heavy 빌드 1개 제약(freeze 이력) — 풀스위트 동반 그룹은 순차, env-only C 는 경량.
- 활성화: 재기동 시 발효(라이브 봇 무접촉, MANUAL 재기동은 Jin). C 는 즉시 env, A/B 는 코드 머지 후 재기동.
