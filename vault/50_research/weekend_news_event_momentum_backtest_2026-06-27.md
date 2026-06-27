---
type: research
status: validated
date_created: 2026-06-27
tags: [research, backtest, weekend, momentum, news-event, maker, okx-spot, reject, flow-not-block]
---

# weekend_news_event_momentum 백테스트 — VERDICT: REJECT (0 빌드)

DEMO/PAPER · aggressive 보존 · flow_not_block · OKX SPOT long-only · -1.0R rail.
backlink: [[weekend_liquidity_range_maker_family_2026-06-27]] · [[weekend_dip_maker_revert_backtest_2026-06-27]]

## 가설
TradFi 닫힌 주말, 크립토가 거시/규제/온체인 뉴스에 혼자 반응 → 주말 momentum continuation.
long-only maker 진입(thrust close 아래 얕은 pullback 지정가, post_only, 스프레드 미크로스).
reversion 패밀리(#1 dip/#2 fade)와 **반대 방향** — 그 리서치가 "주말 down-spike는 continue한다"
(drift -0.046 ATR, MAE>MFE)고 밝혔으므로 continuation 엣지를 long-up-thrust로 검증.

## 셋업
실데이터 yfinance 1H, 10 OKX 메이저/알트/thin, ~730일(~104 주말). thrust 바 = range≥1ATR +
close≥0.65 위치 + volume≥1.4x(뉴스충격 proxy) + up-close. 8바 forward. maker 16bps flat vs
taker(주말 스프레드 6-22bps). OOS 시간반분할 + 평일 control. 스크립트
`/tmp/weekend_momentum_probe.py` · `/tmp/weekend_mom_sensitivity.py`.

## 반증 — continuation 엣지가 주말엔 INVERT (게싱 X, 측정)
- **주말 up-thrust forward drift = -0.067 ATR**(med -0.042, pos% 49 = 코인플립). 주말 thrust는
  continue 안 함 — fade한다. continuation 가설 자체가 주말엔 틀림.
- **평일은 정반대**: 평일 up-thrust +0.114 ATR continue(pos% 50). 주말만 음수. tier별로 더 선명:
  MAJOR 주말 +0.111 / ALT -0.110 / **THIN -0.175**(가장 나쁨). 뉴스가 움직일 thin 알트가 최악 =
  뉴스충격 thesis의 직접 반증.
- **maker net 전 cell 음수**: 주말 maker -13.5~-53.6 bps/trade(win 40-45%), taker -40~-80(win 35-39%).
  maker가 taker를 일관 +26.6bps 이기나(fee 수학 confirm) 음수 신호를 양수로 못 뒤집음 — 패밀리와
  동일 근본(amplitude<cost floor).
- **민감도 그리드 전멸**: thrust 0.8-1.6 × loc × vol × hold 4-12 × follow-through-confirm.
  최고 cell조차 +0.149 ATR(thr1.6/h4, ≈7-13bps gross < 16bps maker). follow-through confirm
  (다음 바도 up이면 진입)은 **악화**(+0.072) — 확인된 주말 continuation이 더 mean-revert.
  volume 확대(뉴스 proxy)는 무효과.

## 추가 제약 (배선 불가)
주말 뉴스-이벤트 트리거를 strategy signal-generator에서 읽을 수 없음 — `news_sentiment` 콜렉터는
EVIDENCE-only로 fuser에만 들어가고 `MarketView`에 노출 안 됨(news_sentiment_collector research의
Deferred ③). 설령 신호 엣지가 있었어도 per-bar 뉴스 트리거 진입은 현 아키텍처상 불가.

## 결론
주말 momentum-continuation(news-event 포함) 진입에 엣지 없음 — reversion(dip/fade)과 **독립 3번째
수렴**: 주말 OKX 신호는 진입 형태(reversion이든 momentum이든) 무관하게 amplitude<16bps maker cost.
→ **REJECT, 0 빌드.** 유일 생존 = `weekend_thin_book_flush_maker`(#77, 이미 배포)뿐.
교훈: 주말 엣지 탐색의 binding constraint는 fee/실행이 아니라 **신호 부재**. 주말 OKX 추가 엣지는
방향성 가격예측(reversion/momentum)이 아닌 **순수 미세구조 수확**(flush의 passive-fill 프리미엄)에서만
나온다 — 그것이 #77이고, 그 한 우물을 더 판다(depth/ladder 튜닝)가 새 방향성 전략 추가보다 정직.
