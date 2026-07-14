---
type: design
status: ready-to-build
date_created: 2026-07-15
tags: [delegation-gate, strategy-ticker, per-ticker-tailored, reset-redesign]
---

# Delegation Gate Blueprint — 진입 전 지능형 전략↔티커 배정

**문제** (2026-07-15 전수 감사): 전략↔티커 매칭 = **dumb asset_class 라우팅**. cci_reversion(commodity 선언)이 FX 물고, weekend_* 세션게이트 0(주중 발화), connors_rsi2 alpaca→capital 누수, session_breakout 등 미등록 발화. 진입 전 지능형 배정 부재 → [[feedback_per_ticker_tailored_gates]] 위반. + 엑싯 백스톱 없어 DIA/CNC 6일 정박(stop null).

## 게이트: 결정론 fast-path + AI slow-path (Jin 2026-07-15 신뢰도게이팅)
```
티커마다:
  피처벡터 조립 [기존 신호 재사용]
  fit_score(전략,티커) 전부 계산 [결정론, 공짜]
  margin = 1위−2위
  ├ margin≥THRESHOLD(확실) → 1위 즉시 배정, AI 안 씀 (대다수)
  └ margin<THRESHOLD(애매) → 상위K만 GPT 타이브레이크 (소수)
```
flow_not_block: 하드 차단 아니라 랭킹·가중.

## 피처 벡터 (원재료 다 존재)
regime_state/regime_v2 · ATR/BB/실현vol 백분위 · 유동성(vol_24h/spread) ·
**히스토리 엣지 = score_f_events 로 (티커,전략) 실제 수익성**(최강신호) ·
인텔피드(COT/funding/earnings/EDGAR/stables/news → 정규화 피처) · correlation_group · 세션/tf.

## fit_score (결정론 주엔진)
전략 엣지타입(추세/회귀/돌파) × 티커 현재거동 + 과거엣지. 셀프-falsifiable.

## AI 에스컬레이션 (애매 슬라이스만)
gpt-5-mini(P0), 이벤트+애매 게이팅 → **~10-40콜/일**(sticky 배정+소수 애매). **섀도우-후-승격**:
1단계 애매케이스 AI 돌리되 결정론 폴백 결정+AI픽 섀도우기록 → AI가 폴백 이기나 실측
2단계 증명되면 애매 라이브결정권 AI로. (G6 GPT 99.97% HOLD 교훈 — 믿기 전 증명.)

## 스캐폴딩 재사용
G6 `evaluate_strategy_swap`(active_strategy_id 교체, 진입후)가 이미 포지션↔전략 뼈대 →
**진입 시점으로 확장**하면 자연히 이 게이트.

## 같이 묶는 2건
- **전략 정렬**: 각 전략 타깃 교정(cci→gold+index만·weekend→세션게이트·connors→alpaca한정) + 레지스트리 재조정(session_breakout 등).
- **엑싯 백스톱**: 시간정지/하드스톱 신설 → stopless 무한정박(DIA/CNC 클래스) 근절.

## 리셋 시퀀스 (phaseable)
storage-split(클린 DB) → 전략정렬 → 델리게이션(결정론 코어 먼저, AI 섀도우 후) → 엑싯 백스톱.
검증: 배정 미스매치 0 · stopless 포지션 0 · fit_score falsifiable · AI콜 <50/일.
