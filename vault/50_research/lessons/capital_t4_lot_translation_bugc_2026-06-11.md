---
type: lesson
status: active
date_created: 2026-06-11
tags: [lesson, capital, sizing, t4, venue-constraint]
related: [[layer-3-sizing-risk]], [[MOC-A1-design-dev]]
---

# Capital T4 → lot 변환 배선 (버그 C) — 고정 $200 노출 종결

DEMO/PAPER. 2026-06-11 빌드. 관련: [[layer-3-sizing-risk]] · `project_exit_decision_vs_close_execution`(memory)

## 무엇이 틀렸나
- `_production_pipeline._real_open_fill` capital 분기가 모든 주문을 `size=MIN_CAPITAL_LOT(1.0)` 고정 전송 — T4 산출 무시. 라이브 216건 fills.size_usd 전부 $200.00 (= 1.0 lot × pip 기본 10 × leverage 20).
- 이중 괴리: fence는 T4 notional(평균 $1,743)을 예약, 실 venue 노출은 epic별 상이(GOLD 1.0 lot ≈ $4,164 / EURUSD 1.0 lot은 min 100 미만이라 전건 거부).
- 의미 오류 2건: `size_usd_to_lots`가 `notional/pip_value_usd`(차원 불일치), `normalize_capital_confirm`이 leverage를 노출에 혼입 (margin ≠ exposure).

## venue 실의미 (RO 프로브 fixture: tests/fixtures/capital_markets)
- `size` = 기초자산 단위 수. `lotSize`=1, `valueOfOnePip` 부재(전 epic).
- 1 unit USD 노출 = `price × lotSize × quote→USD`. quote ccy는 `instrument.currency`(J225=JPY).
- `minDealSize` epic별: GOLD 0.01 / J225 0.1 / EURUSD 100 / NATURALGAS 10.
- size 증분 = `minSizeIncrement`(EURUSD 100 / OIL·NG 0.1 / 지수 0.01). `minStepDistance`는 스탑/리밋 **가격** 거리 — step을 여기서 읽으면 5/6 epic 비정렬(GOLD만 두 키 우연 일치라 GOLD-only 테스트는 못 봄). 블로커 R1에서 minSizeIncrement 우선 파싱으로 교정.

## 고친 방식 (T4 체인 무접촉 — 산출값의 venue 표현만)
- `CapitalConstraintCache`(ProdLoopState 소유): lazy per-epic TTL 3600s, single-flight, stale-serve, 120s negative 쿨다운, size/step 거부 시 evict→강제 재조회. 워밍 epic = 주문 경로 네트워크 0회.
- `translate_capital_order`: fence 예약 전 lots 변환 → requested == submitted == fills.size_usd == position_risk_state.notional_usd 정합.
- 라운딩: floor-to-step(T4 불초과) / sub-min은 ceil-to-min(venue 하한 표현, flow_not_block). fence는 ledger(캡 비교 없음) — min-deal 범프는 수용+관측 로그, 교정된 risk row로 다음 진입 sizing에서 캡 재바인딩.
- 환산: bars → 캐시 snapshot mid → 결손 시 translate 포기(레거시 폴백). JPY rate 결손에 1.0 적용 시 ~160× 노출 오기록이라 의도적 포기.
- degraded 경로 = 현행 거동 byte-identical(1.0 lot + 레거시 수식) + `capital_constraint_fallbacks` 카운터.

## 시계열 단절 (백필 안 함 — 최소 변경)
- 2026-06-11 이전 capital fills.size_usd($200 고정)와 이후(실노출, T4 분포)는 의미가 다름. fee_usd(3bps×노출)·cost_adjusted posterior·learner 입력도 같은 시점에 축 변경.
- bars에 환산 페어(capital:USDJPY 등) 부재 — 현재 snapshot mid 폴백으로 동작. 후속: 환산 페어 universe/bars 배선 검토.

## 라이브 검증 체크 (봇 재기동 후)
- 신규 capital 진입: requested==submitted==size_usd 일치, size_usd $200.00 상수 탈피(T4 분포).
- 실노출 회계로 ReservationConflict/sizing_zero/rotation 후보 증가는 교정의 올바른 결과(throttle 아님) — 첫 24h 관측.
