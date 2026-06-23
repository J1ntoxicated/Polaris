---
type: research
status: design
date_created: 2026-06-24
date_updated: 2026-06-24
tags: [research, strategy, alpha, exit, fee, slippage, flow-not-block]
---

# 나머지 active 전략 전수 진단 — fee/leverage·엑싯비대칭·진입무엣지

DEMO/PAPER · aggressive 보존 · flow_not_block(차단/사이즈컷/진입블록 아님). read-only 진단.
backlink: [[equity_tsmom_edge_diagnosis_2026-06-24]] · [[flow_pressure_crypto_profit_tuning_2026-06-24]]

## 회계 발견 (load-bearing)
fills.pnl_usd 는 fee 미포함 — fee_usd 는 별도 컬럼. **true net = pnl_usd − fee_usd.**
이로 인해 가격-양(+)인 3전략(session_breakout +$1.88 / xau_indices_trend +$0.43 / fx_range_fade +$14.44)이 fee 차감 후 전부 net-음(−)으로 뒤집힘. fee 가 책 전체 최대 은닉 출혈(flow_pressure 단독 $1038).

## 전략별 1줄 진단 (병형)
- **equity_gap_go** (alpaca, n16): 엑싯늦음+슬리피지. illiquid 마이크로캡 갭 1D 따라가기, +0.27R MFE→−0.5R round-trip, 73bps(max644) 슬리피지. 진입엣지도 약(6%승).
- **burst_rider** (okx, n45): 엑싯빠름(winner)+fee. winner MFE +1.98R(peak +7.33R)인데 실현 +0.137R로 breakeven 평탄화. 50 round-trip $133 taker fee가 −$45 가격손 압도.
- **session_breakout** (capital, n50): fee/churn+엑싯빠름. 가격 +$1.88인데 51 close × $3.24 fee = −$165. winner +0.77R MFE를 +0.009R로 banking.
- **micro_reversion** (okx+capital, n50): 진입무엣지+즉시역행fill(okx). 38/50 ≤2s, MFE 0, −5.2R 슬리피지 tail(FLOKI/BNT). Capital leg 대칭payoff(엣지無).
- **fx_breakout_basket** (capital, n17): fee-on-leverage+약한 엑싯늦음. Donchian-40 가격~flat, 30x notional × $7/close = −$120.
- **tsmom** (okx, n5): dormant 진입무엣지. pre-reset 5건 0승 MFE +0.08R, 비활성.
- **xau_indices_trend** (capital, n14): fee-only. 가장 건강(mae −0.14R tight, winner>loser hold, 가격+). $34 fee만이 유일 누수.
- **fx_range_fade** (capital, n4): fee-on-leverage가 GOOD 신호 살해. 75%승 +0.68R MFE tight MAE(원하는 비대칭)인데 $12/close × 4 = −$48이 전부.
- **volume_burst** (okx, n1): dormant 단일표본. n=1, 즉시 stop.

## 손실 기여 순위 (true net, 큰 출혈부터)
1. equity_gap_go −$588 (alpaca, 슬리피지+엑싯늦음 — 나머지 중 최대 단일 출혈, 순수 메커니즘)
2. burst_rider −$179 (가격 −$46 + fee $133)
3. session_breakout −$163 (가격 +$2 − fee $165)
4. micro_reversion −$142 (okx 즉시역행 무엣지 + capital 대칭, − fee $110)
5. fx_breakout_basket −$136 (가격 −$15 − fee $120)
6. tsmom −$34 (dormant) · 7. xau_indices_trend −$34 (fee-only, healthy) · 8. fx_range_fade −$33 (fee가 GOOD 신호 살해) · 9. volume_burst −$5 (dormant)

## 3대 병형 + 후보 방향 (전부 flow_not_block, 사이즈컷/블록 0)
1. **fee/leverage-notional 출혈** (지배적·전 Capital+OKX): fee가 full leveraged notional(30x FX/20x index)에 부과 → 얇은 엣지가 못 넘음. **maker/limit 진입으로 taker→maker 전환 + leg 통과 hold로 churn 감소.** 사이즈컷 아님.
2. **엑싯 비대칭(payoff 역전)**: lock +0.20R·give-back 60% harvest가 winner를 breakeven 평탄화(burst +7R→+0.001R). 반대로 loser는 floor 늦게 arm. **momentum/breakout family let-winners-run trail(peak 분율 lock) + round-tripper는 floor 조기 arm(~+0.15R BEP).**
3. **진입무엣지+즉시역행fill(illiquid)**: micro_reversion OKX / equity_gap_go 갭. **진입 follow-through 확인틱(flow_pressure flow_confirmed 패턴) + 유동/tight-spread 심볼 한정.** veto/throttle 아닌 timing 정밀.

**최고 레버 단일수정 = Capital maker-limit 진입 라우팅** — fx_range_fade·xau_indices_trend·session_breakout 를 가격-양 위에서 net-양으로 한 번에 뒤집음.
표본충분: micro_reversion(247)·session_breakout/burst_rider/fx_breakout(17-54) characterizable. tsmom(5)/volume_burst(1) dormant pre-reset.
