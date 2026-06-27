---
type: research
status: validated
date_created: 2026-06-27
tags: [research, backtest, weekend, mean-reversion, maker, okx-spot, fee, reject, flow-not-block]
---

# weekend_dip_maker_revert 백테스트 — VERDICT: REJECT

DEMO/PAPER · aggressive 보존 · flow_not_block · OKX SPOT long-only.
backlink: [[weekend_liquidity_range_maker_family_2026-06-27]] (패밀리 design) · candidate #1 (VWAP-이격 dip).

## 셋업
실데이터 yfinance 1H, 12 심볼(major/alt/thin), **729일 span (~104 주말)**. OOS 시간반분할.
진입 = 주말(Sat/Sun UTC) intraday z-score(close-VWAP dev) ≤ -1.5 dip. maker = best-bid 아래 5bps 지정가 + adverse-selection skip 모델(빠른 favorable gap 미체결). 엑싯 = let-run revert target + -1.0R ATR rail + peak-protect. maker fee 16bps flat vs taker 30-62bps(주말 스프레드 1.6x).
스크립트: `/tmp/weekend_maker_backtest.py` · `bt_v2.py`(let-run 엑싯 cfg sweep) · `bt_ideal.py`(frictionless 신호 probe).

## maker 가설 = 절반 confirm / 절반 refute
- ✅ **fee 수학 뒤집힘 confirmed**: maker RT 16bps vs taker 30-62bps. maker net이 모든 cell(major/alt/thin·IS/OOS)서 taker net을 이김. win% maker 48-51 vs taker 15-28.
- ✅ **주말효과 real**: 주말 dip(z<-1.5) fwd +8.5bps/6h(med +10.9, 54% win) vs **평일 dip -1.0bps/50%**. 알트>메이저(알트 +14bps vs 메이저 +4bps), 깊은 dip>얕은(z<-2.5 +19bps/3h) — 둘 다 directional 성립.
- ❌ **그래도 진다**: 신호 진폭(idealized frictionless 4-14bps)이 **maker cost floor 16bps 아래**. `covers_16bps_maker` = 전 cell False. maker net -18bps/trade(PF 0.45), taker net -43bps(PF 0.15).

## 치명타 2개
1. **gross edge ≈ 0**: filled-set gross -12~-5bps (let-run cfg sweep 4종 전부 음수). 16bps maker cost가 그걸 확정손실로.
2. **fill-risk = free 아님 (가설 오류)**: adverse selection이 강한 V-recovery(+19bps deep-dip revert)를 skip → maker가 best revert tail 놓침. cfg2/3서 taker gross(+8bps)>maker gross(-5bps)인 selection artifact가 증거. "놓친 fill = 0 실현손실"은 맞으나 **놓치는 게 하필 엣지원천**이라 기대값 훼손.

## 결론
maker가 taker fee 재앙(-43bps)을 느린 출혈(-18bps)로 줄이나 **흑자 전환 못함**. 주말 dip-revert 신호는 real이지만 amplitude<cost. → **REJECT** (TAKER_ONLY도 아님 — taker가 더 나쁨).
candidate #2(weekend_liquidation_fade)와 **동일 근본**(adverse selection이 엣지 tail 역선택)에 독립 수렴 → fade-the-spike·dip-revert 두 진입 형태 모두 폐기. candidate #3(funding_oversold_maker_accumulate)는 다른 진입신호라 별도 검증 필요.
교훈: maker가 fee를 이기는 건 참이나 **fee가 binding constraint가 아니었다 — 신호 부재가 binding**. 거동 변경 가치 없음 → /debate·빌드 불필요.
