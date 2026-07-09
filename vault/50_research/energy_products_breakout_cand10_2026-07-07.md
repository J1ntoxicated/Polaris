---
type: research
status: validated
date_created: 2026-07-07
tags: [research, backtest, oos, breakout, energy, commodity, capital, reject, candidate10, fee-hurdle]
---

# candidate#10 Capital energy-products 1D breakout — REAL DB bars OOS — VERDICT: REJECT

DEMO/PAPER · aggressive 보존 · flow_not_block · Capital CFD long-only. Strategy-expansion wave candidate#10.
backlink: [[fx_donchian55_dbbars_oos_2026-07-07]] (동일 방법론·동일 sample-starved 교훈) · [[okx_donchian_55_breakout]] · [[project_validated_edge_is_slow_trend_not_scalp]].

## 질문
MAP candidate#10 = HEATINGOIL/GASOLINE/NATURALGAS "seasonal-momentum breakout" (난방유 겨울 / 가솔린 여름 계절성).
실 DB(data/polaris_live.sqlite, Capital 1D)에서 energy-product 모멘텀 브레이크아웃이 fee-hurdle을 OOS로 넘는가.

## 셋업
- 실데이터 1D: HEATINGOIL 527 · GASOLINE 505 · NATURALGAS 491 · GASOIL 248 bars (~1.4yr, 중복제거).
- 계절성 주석: ~1.4yr = **계절 사이클 <2회** → 캘린더-시즈널 오버레이는 OOS 검증 불가. 정직한 검증 코어 =
  시즈널리티가 *생산해야 할* energy-product 모멘텀 브레이크아웃.
- 신호: okx_donchian_55 진입 EXACT 복제 — close > prior-D-high(`bars[i-D:i]`, no lookahead) AND ROC-20>0.
  채널폭 D-55/D-30/D-20 3종 스윕(bar수 대비 발화 충분 채널 탐색). 검증 let-run 엑싯(entry-2·ATR20 stop + close<D-20 harvest + 25b backstop).
- fee: Capital energy RT 실측(quote_ticks 2026-07-07: HEATINGOIL 6.0 / GASOLINE 6.7 / OIL 4.6-6.2bps; NATURALGAS 부재→보수 10) + slip 0/5/10/15/side.
- gate: walk_forward_splits + pbo(perf matrix 11 configs × 6 paths) + admit_strategy + 2-half OOS + frictionless fwd-return.

## 결과 — 명확한 REJECT

**per-symbol breadth 붕괴 (winner-carried autopsy pattern):**
| 심볼 | D-30 net@15 | frictionless fwd(h20) | 진단 |
|---|---|---|---|
| GASOLINE | **+463bps** win0.48 | +765bps win0.72 | 유일 winner — 단, 2026 가솔린 랠리 1개 블록 집중 |
| HEATINGOIL | **−229bps** win0.28 | +523bps win0.50 | net 음수, fwd win<0.5 coin-flip |
| NATURALGAS | **−533bps** win0.39 | **−521bps** win0.54 | 진입 엣지 **부재**(fwd 전 horizon 음수, 브레이크아웃이 역행) — 엑싯 튜닝 구제 불가 |
| GASOIL | +588bps | +426bps | n=4~12 sample-starved, IS/OOS 극단 불안정(+3446/−1175) |

**formal gate (pooled net@15, 3 채널 전부):**
- deflated_sharpe = **0.000** (floor 0.60) — 전 채널.
- OOS pooled Sharpe 0.03~0.12 (≈0). IS→OOS 스프레드 erratic(HEATINGOIL/GASOLINE/GASOIL 반분할마다 부호 뒤집힘 = 국면 운).
- **PBO = 0.60** (ceiling 0.30) — 11 configs × 6 paths CSCV. perf matrix상 "엣지" 전량이 **path col#5 단일 블록**(mid-2026 energy 랠리)에 집중, 나머지 전부 음수. 국면-운, 안정 엣지 아님.

**GASOLINE 단독 유혹 (OOS mean +611~+814bps, OOS Sharpe 0.37~0.43):**
정확히 recipe가 금하는 winner-carried 함정 — 4개 중 1개 심볼이 mean을 지고, body(HEATINGOIL 음수 · NATURALGAS 진입엣지 부재)가 드래그.
GASOLINE-alone조차 엣지가 ~2개 클러스터 블록(2026 랠리)에 집중, OOS Sharpe 0.37~0.43은 admission bar 미달, deflated Sharpe(12 configs 시행 반영)=0. 1개 럭키 랠리 블록 위 단일심볼 전략 빌드 = 게이트가 존재하는 이유인 바로 그 overfit.

## 진단
- 비용 문제 아님(energy 스프레드 4.6~10bps는 tiny). **breadth 부재 + 국면집중**이 dominant.
- NATURALGAS = 브레이크아웃 역행(mean-revert) 상품 — energy라고 다 trend-persistent 아님.
- ~1.4yr DB는 energy 계절 basket을 formal OOS로 admit하기엔 사이클 부족. FX D-55와 동일 sample-starved 계열.

## VERDICT: REJECT
- ❌ candidate#10 등록 금지. STRATEGY_REGISTRY 미투입.
- deflated_sharpe 0.0 (<0.60) + PBO 0.60 (>0.30) + winner-carried(GASOLINE 단독 carry, NATURALGAS 엣지부재) → 3중 탈락.
- 재검토 조건: 계절 사이클 ≥3회 데이터 확보 후 GASOLINE 단독을 별도 per-ticker 후보로(basket 아님), 또는 energy는 OIL_BRENT/OIL_CRUDE(candidate#1) trend가 우선.

스크립트: scratchpad `cand10_energy_bt.py` (세션 스크래치, 방법론 본문 기재로 재현 충분).
