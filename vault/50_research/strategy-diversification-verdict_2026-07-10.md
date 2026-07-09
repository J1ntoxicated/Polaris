---
type: research
status: verdict
date_created: 2026-07-10
tags: [strategy-diversification, p0a, evolve, candidate-factory, hybrid, pts]
---

# 전략 다양화 — 수동 리서치 vs 생성기 verdict (2026-07-10)

DEMO/PAPER 전용(가상). aggressive/flow-not-block. 코드/DB/봇 무접촉 감사 기반.

## 판정: (c) 하이브리드 — 단, "새 생성기 빌드"가 아니라 **이미 결정된 단계 계획(P0a→P2-if-proven)을 재개**
질문은 이미 2026-06-01 /debate에서 판결남([[p3_self_evolve_2026-06-01]]): **"장치 전에 edge 먼저"**. 생성기 *엔진*은 이미 존재 — `polaris/core/evolve/`(~1100 LOC) = 변이 enumerate + embargo-purge walk-forward IS/OOS + **trials-aware honest-N DSR**(다중검정 편향 보정, trials↑→verdict 엄격, monotone) + positive-control(N-굶음 vs 피처고갈 판별) + offline trial registry(`data/p0a_registry.sqlite`, 라이브 미접촉). 내가 "명시하려던 과최적화 가드가 이미 구현·정확".

그래서 (a)순수수동 = 검증기계(virtual+PtS+L4/L5) 굶김 + yfinance→DB flip 무덤 재생산(수동 eyeball 게이팅이 근본원인). (b)순수생성기 = 새 archetype/방향(숏/carry/event=실제 갭) 구조적으로 못 만듦 + 엔진 이미 존재 = 재발명. → **정합축(기존 검증인프라)이 하이브리드를 강제**: P0a가 정직한 OOS 생존자 생산 → PtS/virtual이 prove·선별. 빠진 건 그 둘 사이 **배선 1개**.

## 왜 지금 재개하나 (2026-06-01 blocker 2개 해소)
- 그때 #1 반론 "검증스택 greenfield+굶음" → **해소**(virtual account·PtS FSM·score_F·L4·L5·Kelly/tier 모두 라이브).
- 마지막 P0a 판정 = **VALIDATION_STARVED**(N=53<69, log d7cf7f3) — held-out 바 부족 + 그리드가 OKX-intraday/fx(volume_burst·spot_donchian·tsmom 등 **이후 전부 KILL**). EARN 검증 패밀리(gold/index/equity)는 스파이크 당시 **미존재**. → 판정 stale, DB 深化로 N-굶음도 해소.

## 즉시 후보 (정직)
- **hand-build 대상 0개.** `vx_squeeze_1h_crypto4`(감사B "검증됨-미구현") = 리서치 재검증서 **실 DB REJECT**(IS 음수, yfinance flip) → 빌드 금지. carry/short/vol 갭 3후보 = 전부 NO-GO. → 이게 하이브리드의 증거: 수동 "검증됨"이 실 DB서 증발 = 균일 honest-N 게이트 부재.
- 즉시 = 전략 아님 **프로세스**: EARN 패밀리로 P0a 재실행(BG1) → SEARCH_SPACE_EXISTS면 배선(BG2). 1st 캠페인 = **검증 breakout archetype을 전 심볼 유니버스로 clone**(수동 silver/us100/uk100 cloning 자동화, honest-N 게이팅) — 생존자 신뢰도 최고 지점.

## 기술 nuance (build 정확도)
- breakout EARN(gold/silver/us100/uk100 = 순수 Donchian 크로싱, window LOCKED) → **grid 비어있음 → SYMBOL/TF breadth 축**이 레버.
- reversion/momentum EARN(connors `rsi_entry=10`·cci level·index_52w proximity/ROC floor·macd pullback) → **PARAM grid 축**(threshold knob) + symbol breadth.

## 과최적화 가드 (대부분 이미 구현, 배선에 3개 추가)
OOS 홀드아웃=embargo-purge WF가 **유일 승격면**(oos_pass만, IS-pass 절대 아님, 구현됨) · trials-deflated DSR(구현) · 파라미터 안정성=≤3점/param·GRID_CAP 200·frozen SSOT(구현)+*승자는 고립 grid-corner 금지*(인접 cell 동반 pass, 신규) · 최소표본=positive-control min_passable_n(구현)+*생존자 ≥k 독립 cell*(winner-carried 방지, 신규) · **라이브-forward virtual PROVE backstop**=배선 생존자는 PROVE로만 진입(EARN 아님), virtual account 실사이즈로 재증명 후에만 PtS→EARN(위조 불가 최종가드) · cohort cap N(throttle, 신규).

## 빌드그룹 → JSON(반환)
BG1 검색 활성화(data+driver breadth) · BG2 survivor→virtual-PROVE 배선(신규 1선) · BG3 병렬 수동 archetype 트랙(숏/carry/event = thesis-first base class 저작 후 P0a에 투입, blind sweep 금지).

Related: [[p3_self_evolve_2026-06-01]] · [[now-archive-2026-06-11-p2]] · project_validated_edge_is_slow_trend_not_scalp · feedback_virtual_account_first_then_real_wire
