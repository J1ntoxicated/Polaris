---
type: research
status: research-converged
date_created: 2026-06-25
tags: [research, strategy-expansion, horizon-regime-matrix, backtesting, event-regime-probe]
---

# 전략 확장 로드맵 — 호라이즌×레짐 매트릭스 + 백테스팅 + 이벤트프로브 (w61qop5c5)

## 정정 (evidence-based, super-brain 적발)
- **A 백테스트**: replay에 PSR/DSR/purged-WF "이미 있다"=환각. 실제 5파일(engine·equity_curve·fill_model·models·sandbox_db)만 → overfit 툴링 **신규 빌드**.
- **B regime**: bear_trend OKX롱 AMPLIFY 오정렬=코드 확인(참). `score.py:_TREND_REGIMES={bull_trend,bear_trend}` 둘 다 AMPLIFY → bear서 long-only 역풍.

## 매트릭스 빈셀 (edge 부재 = 거래 침묵 원인)
crisis 열 전체 · bear_trend×{scalp,position} · **chop×scalp(tick-MR 無)=OKX chop 침묵 직접원인**. crisis는 fuser가 판정(vix>40/hy>500)하나 score.py 전부 NEUTRAL=edge 0.

## 추가 전략 (우선순위)
- **P-A** [코드만·즉효] bear_trend OKX롱 AMPLIFY→DAMPEN(0.8). flow_not_block(strictly-positive redistribution, 차단/0 아님). 오정렬 수정.
- **P-C `vwap_band_fade_scalp`** [chop×scalp·OKX틱·롱only] VWAP±2σ fade+ADX필터(55-65%WR). chop×scalp 빈셀=OKX chop 거래 핵심.
- **P-B `xau_crisis_breakout`** [crisis×swing·Capital금] 위기 금돌파(2008·2020 검증). crisis 빈셀.
- **P-D `safe_haven_carry_unwind`** [risk-off·Capital fx JPY숏] carry-unwind. risk-off 레이블 신설=/debate.
- **P-E `equity_defensive_absmom`** [bear/crisis·Alpaca] Antonacci abs-momentum 필터.
- 매핑보정: connors_rsi2·equity_rsi_bb=uptrend-pullback군(chop-MR 아님).

## 백테스팅 (하이브리드)
자체 replay 확장(live-parity 보존, 외부 통째도입 거부). P0=replay 멀티-호라이즌 동시합주(bar_interval 단일→{1m,1H,1D} sub-book 병렬→공유자본 합성). +overfit 툴링 신규(CPCV+DSR 승인게이트, #13 학습오염 연결).

## 이벤트 레짐 프로브 (surgical-strike)
S1 `vix_state`(보유 fred_macro.vix, 키0)→AltDataView 노출. S2 `MacroCalendarCollector`(FRED releases/dates, 무료키)→FOMC event_state. 이벤트=entrance 4번째 lens(현 3). 티커별 영향=correlation 매핑.

## 봇모델 패턴 차용
1 Thompson Sampling 전략배분(L4/L5, per-strategy×regime Beta posterior→cell mult bandit, /debate) · 2 CPCV+DSR 승인게이트 · 3 이벤트프로브(#7 연결).

## 빌드순서 권고
P-A(즉효 버그수정) → P-C(OKX chop 거래) → S1 vix_state → replay 멀티-호라이즌 → P-B crisis → overfit 툴링 → P-D/risk-off(/debate). 전부 flow_not_block·9-stack·DEMO·거부키워드0.

## 관련
[[multi_horizon_activation_2026-06-25]] · [[multi_horizon_architecture_design]] · [[north-star]] · 전문=w61qop5c5.output
