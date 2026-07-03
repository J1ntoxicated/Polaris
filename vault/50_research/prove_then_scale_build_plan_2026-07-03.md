---
type: research
status: active
date_created: 2026-07-03
tags: [prove-then-scale, build-plan, classes, sizing, r-budget, g5, telemetry]
related: ["[[prove_then_scale_classes_2026-07-03]]", "[[layer-3-sizing-risk]]", "[[profit_sweep_ladder_2026-07-03]]", "[[ADR-005-sizing-formula-cell-routing|ADR-005]]"]
---

# Prove-then-Scale — 파일 레벨 빌드 플랜 (2026-07-03)

> DEMO/PAPER. Aggressive=자본 라우팅(차단 X). 9-stack 봉쇄·-1.0R rail 불변. 스펙=[[prove_then_scale_classes_2026-07-03]] §빌드 스펙 8항목. score_F/strategy_class/probe_notional/EXEC_STARVED=코드 내 전무(전부 net-new).

## 빌드 그룹 (파일 무겹침 = 병렬 워크트리)
| G | 파일 (신규+/수정~) | 스펙 | 후킹 |
|---|---|---|---|
| **A 저장** | +`storage/schema_ddl_classes.py`, ~`storage/schema.py:258`(ALL_DDL append), ~`core/lifecycle/recover.py`(+hydrate_strategy_class, reuse :162 GROUP-BY 패턴) | ①⑦ | ALL_DDL tuple :258 · `BEGIN IMMEDIATE` :268 |
| **B 판정** | +`core/classes/score_f.py`(per-trade fee-norm=Σ net_i/max(\|fee_i\|,1bp×notional), flat→nonzero→flat) | ② | 청산 이벤트=`recover.hydrate_open_positions` 집계원 |
| **C 전이** | +`core/classes/transition.py`(Schmitt+dwell+tripwire+사다리, 상승1/24h·강등∞·KILL) | ③④ | rollup 소비 score_F(G-B) |
| **D G5분기** | ~`core/sizing/engine.py:642`(compute_size 진입 class 분기: EARN=현체인 / PROVE=probe 상수 / BENCH=shadow), +`core/sizing/probe_notional.py`(admission stop_dist>3×왕복fee 미달=shadow) | ④ | compute_size :618 · shadow swap :915 재사용 |
| **E R-pool** | +`core/classes/r_pool.py`(BENCH freed R→intra-track, probe 선차감, EARN 가중), ~`engine.py:850`(headroom_min 0.5×track_R 항 추가) | ⑤ | ladder `bucket_available_usd`/`draw_for_signal` 패턴 재사용 |
| **F 랭커** | +`tools/ops/probe_reranker.py`(07:30 pessimistic-shadow score_F desc, tie fill_rate→fee→age) | ⑥ | launchd 07:30(`daily_restart` 인접 신규 plist) |
| **G 텔레** | ~`tools/ops/watchdog.py:35`(EXEC_STARVED·binding-cap·probe-fee 소진 alert key), ~`engine.py`(binding 로그 확장) | ⑧ | log_scan 패턴매치 :15 · alerting :32 |

## 그룹 간 의존 순서
`A → B → {C, D} → E → F → G`. A=스키마 SSOT(전부 read). B=score_F(C·F 소비). C·D 병렬(class read만 공유, 파일 무겹침). E=engine.py 수정 → D 이후(같은 파일). F·G=terminal(관측만).
워크트리 병렬 가능: **{C}∥{F 스켈레톤}** · **{B}∥{A 후반}**. D·E는 engine.py 직렬(같은 파일).

## 발화경로 검증 (등록≠발화)
- **D**: compute_size 3콜러(entry_sizer.py:78 라이브 / replay:261 / tick_engine:217) 전부 class-aware 확인. PROVE→실제 probe intent가 order submit 도달(shadow ring 아님) 단언.
- **C**: 전이 이벤트가 `strategy_class.class` 실제 UPDATE + 다음 compute_size가 새 class read. dwell/tripwire 경계 트리거 로그.
- **F**: 07:30 rerank가 slot 실제 재배정(등록만 X) — probe_fee_24h 동결값 소비 확인.

## 테스트 전략 (TDD 실패 먼저)
- B: `test_score_f.py` — 부분체결·scale-in/out 합산, fee-norm 경계(fee=0→1bp floor). 실패→구현.
- C: `test_transition.py` — Schmitt 비대칭(0.4W<-1 승격 / <-4 강등), dwell PROVE10/EARN5, tripwire W8<-4 즉시, 상승1/24h·강등∞, KILL 자동부활 불가.
- D: `test_g5_class_branch.py` — EARN=byte-identical 현체인, PROVE admission(stop_dist>3×fee 미달→shadow), BENCH=R 0.
- E: `test_r_pool.py` — freed R intra-track only, zero-EARN=idle(강제 probe X).
- A: `test_idempotent_classes_ddl_twice.py` — hydrate 재기동 리셋 금지.

## 9-stack 무결 증명 포인트 (probe=상수·min() 항 확장만)
1. **PROVE probe = T4 체인 밖 상수**(스펙②): `<1.0 mult 아님` — engine.py:673 raw_cont_preclip 곱셈 슬롯 미접촉. probe_notional.py가 compute_proposed 우회(별도 반환), 곱 스택 0증가.
2. **0.5×track_R = min() 항 추가**(스펙⑤): engine.py:844 headroom_min 기존 8항 → +`0.5×track_R` **순수 min() 항**(곱 아님). ladder additive(:837)·single_trade sub-terms(:801-806) 동일 허용 패턴.
3. EARN=풀 T4 불변(스펙⑤ 계급≠multiplier) — cont/tier/cell/list 슬롯 수 불변.
4. property test: EARN unclipped ≈ 현 체인 ±0(회귀), PROVE=상수 확정.

## UNKNOWN / 리스크 (통합)
- **U1** `0.5×track_R` cap 항 semantics: engine.py:783 `track_rem`=track_gross_cap-used(daily 아님). 스펙⑤ "0.5×track_R"이 track_gross_cap 절반인지 별도 R-pool basis인지 spec 미명시 → **E 착수 전 확정 필요**.
- **U2** probe_notional K vs BASE_RISK_PCT: fee-floor K=3(exit_strategy_config.py:132) vs R-budget BASE_RISK_PCT=0.02(risk_unit.py:264) — 스펙② "K=3×왕복fee, venue min"은 fee-floor K. 두 상수 별개 확인.
- **U3** score_F rollup 저장소: 청산 이벤트원=hydrate_open_positions이나 flat→nonzero→flat lifecycle 집계 SSOT 부재 → B가 신규 정의(fills/positions_strategy_segments 재사용 여부 확정).
- **U4** EXEC_STARVED: 전용 이벤트 로그 전무(STALL은 warning만, 구조화 파싱 X) → G가 신규 tag+log_scan 패턴 추가.
- **U5** strategy_class 테이블 부재 확정(schema 전수 0건) — pending_opens `partial_trueup` drift 유형 회피: PK=(venue,strategy_id,class) UPSERT+partial UNIQUE로 idempotent.
- **U6** WAL creep: rollup/rerank는 hot-path 밖(07:30 배치·청산시). ladder sweeper처럼 batch commit, close 루프 write-free 유지.
