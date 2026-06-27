---
type: research
status: validated
date_created: 2026-06-27
tags: [research, backtest, weekend, momentum, directional, maker, okx-spot, fee, reject, flow-not-block]
---

# weekend_gap_drift 백테스트 — VERDICT: 0 BUILD (전 후보 REJECT)

DEMO/PAPER · aggressive 보존 · flow_not_block · OKX SPOT long-only · -1.0R rail.
backlink: [[weekend_liquidity_range_maker_family_2026-06-27]] (reversion 패밀리, REJECT) ·
[[weekend_dip_maker_revert_backtest_2026-06-27]] · [[strategy_vs_execution_partA_2026-06-24]].

## 과제 (reversion 패밀리와 차별)
기존 주말 리서치는 전부 **reversion**(dip-revert/liquidation-fade/flush)이고 전부 REJECT
(신호 진폭 < 16bps maker cost, adverse selection이 엣지 tail 역선택). 이번 각도는 **directional/momentum**
— TradFi 닫힌 주말 크립토 고유 drift. 3 가설, 전부 OKX SPOT long-only + maker:
- **H1 주말 추세지속**: 주말 intraweekend Donchian high 돌파 → 주말 잔여 시간 UP drift 지속 (thin-book = 방향성 persist).
- **H2 일요일밤 pre-Monday drift**: Sun 저녁 UTC 윈도우가 Monday gap 선행.
- **H3 Fri→Sat 부호가 Sat→Sun 지속 예측** (주말 추세 persistence).

데이터: yfinance 1H, 12 심볼(major/alt/thin), **729일 (~104 주말)**, OOS 시간/연도 split,
maker RT 16bps flat(스프레드 크로스 X), -1.0R ATR rail + bounded target, maker fill = adverse-selection skip.
스크립트: `/tmp/weekend_gap_drift_probe.py`(raw drift) · `/tmp/h2_sunday_rail_probe.py` · `/tmp/h3_persistence_rail_probe.py`.

## H1 주말 돌파지속 = REJECT (coinflip + fat tail)
9-cell 그리드(lookback 6/10/18 × hold 4/8/12). raw forward drift mean이 일부 cell +15bps이나
**median ≈ 0, pct_positive 49-50% = 코인플립**. mean을 fat right tail이 끌어올린 것. 16bps maker 차감 후
거의 전 cell 음수(best cell lb10_hold8 = **−0.19bps ≈ 0**, OOS −1.26). 돌파 후 지속 엣지 없음.

## H2 일요일밤 drift = REJECT (rail이 raw drift의 거짓을 폭로)
raw drift는 Sun 20:00z entry에서 **median +35~48bps · pos 56-60%** 였으나 **mean 음수**(드문 주말 crash가 mean 폭파).
→ raw drift mean은 **틀린 통계**. 실제 시스템 -1.0R rail 시뮬:
- 18-cfg 전부(Sun 20/22z × hold 6/8/10 × tgt 0.5/0.8/1.2R) **avg_net_R −0.19~−0.36, IS·OOS 둘 다 음수, PF 0.37-0.75**.
- win 53-58% · median net-R 양수(+0.11~+0.36)인데 **mean 음수** = 전형적 negative-skew: 작은 승 다수 vs rail-hit(40-54%) 대형 손실.
- long-only에 비대칭이 **거꾸로**: 드문 주말 down-cascade가 full −1.0R, 상방은 target에 capped.
- **maker ≈ taker** (−0.36 vs −0.36) → fee가 binding 아님, 신호가 binding.

## H3 Fri→Sat→Sun persistence = REJECT (per-symbol 양수 = small-sample 노이즈)
raw probe서 BTC/BNB/XRP/ADA가 +net-16(ADA +74bps, **n=50**)였으나 — **pooled(전 심볼) + rail 적용**:
- tgt 0.5/0.8/1.2R 전부 **POOLED avg_net_R −0.48~−0.54, PF 0.28-0.45, IS·OOS·전 asset-class 음수** (pooled n=676).
- 체리픽 양수는 n~50 노이즈였음. Fri→Sat UP 요구 = LONG으로 천장 매수 → rail이 Sunday reversal 포획.

## 결론: 0 BUILD
주말 directional/momentum 3 가설 전부 robust REJECT(OOS·pooled·rail·12심볼). 핵심:
- **maker ≈ taker 등가가 3 가설 전부에서 재확인** → reversion 패밀리와 **독립 수렴**: binding constraint는 fee가 아니라 **신호 부재**.
- 주말 크립토는 directional persistence도 reversion amplitude도 16bps를 못 넘긴다 — 주말 thin-book은 **방향성을 만들지 않고(코인플립) 변동성만 키운다**(MFE/MAE 둘 다 확대).
- 유일 생존 = 이미 배포된 **weekend_thin_book_flush_maker**(#77) — deep oversold RSI<25 + 하단밴드 wick capitulation을 deep passive bid로 수확하는 **특정 microstructure 프리미엄**. 일반 drift/breakout/persistence엔 그런 프리미엄 없음.
교훈 재확인: 주말 윈도우 가설은 cohort-direction은 맞으나(주말 ≠ 평일) "주말이 distinct" ≠ "주말이 tradeable directional edge". → /debate·빌드 불필요.
