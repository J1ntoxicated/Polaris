---
type: research
status: design
date_created: 2026-06-24
date_updated: 2026-06-24
tags: [research, strategy, execution, exit, slippage, fee, flow-not-block, partA]
---

# PART A — 전략 vs 실행 분리: "전략이 나쁜가, 실행이 망치는가"

DEMO/PAPER · aggressive 보존 · flow_not_block(차단/사이즈컷/진입블록 아님). read-only, 증거기반(data/polaris_live.sqlite).
backlink: [[all_strategy_edge_diagnosis_2026-06-24]] · [[equity_tsmom_edge_diagnosis_2026-06-24]]

## 결론 (load-bearing): 둘 다지만 **실행이 주범** — 신호엔 edge가 있다
hold-time이 길수록 성과 단조개선(flow_pressure <1s −0.60R → >5m −0.075R). winner는 다 있는데 MFE를 못 챙김:
**winner MFE-capture 비율 = burst_rider 4% · session_breakout 1% · fx_breakout 0% · flow_pressure 19% · micro_reversion 28% · fx_range_fade 41%.**
유일한 net-양 전략 = fx_range_fade(+0.25R, 41% capture) = **전용 fade-exit(profit_target_r) 받은 단 하나** → "엑싯 고치면 산다"의 직접 증거.

## 거래량 실체 (중요 — 로스터 ≠ 실제 트레이더)
실거래 91% = **틱엔진 신호**(flow_pressure 1232 + micro_reversion 393 + burst_rider 92 = 1717/1895 closed). 크립토 bar전략 8종(volume_burst/tsmom/rsi_bb/spot_donchian/ema/connors/supertrend/cci)은 **signals 0건** — 변동성알트에 발화하나 US-OKX 51155 blocklist로 trade 불가(_NOW #4). equity/CFD bar전략은 signals 다수(equity_tsmom 6809)나 fill 전환 극소(→37 closed).

## 2대 실패모드 (명확히 분리됨)
**모드1 — 진입 mistiming (신호/타이밍 문제):** flow_pressure <5s 337건 avg_mae −1.85R · mfe ≈0. 진입즉시 역행, favorable excursion 전무. "59% mfe<0.2 진입오판" 패턴. (단 flow_confirmed fix는 06-23 적용 — 이 코호트는 fix前.)
**모드2 — winner의 MFE giveback (순수 엑싯 문제):** mfe>0.3R 도달 384건이 +0.036R로 실현, 133건은 loss로 반납. lock+0.20R / give-back 60% harvest가 momentum winner를 평탄화.

## 정직 $ 원장 (fills is_close, fee 별도=true net=pnl−fee)
venue: alpaca −$1361(55) · okx −$521(+fee $636) · capital −$74(+fee $492). **OKX 틱 슬리피지 11.24bps** = 0.5R 스캘프 edge를 round-trip에 잠식. fee가 책 최대 은닉출혈([[all_strategy_edge_diagnosis_2026-06-24]] flow_pressure fee 단독 $1038).

## 전략별 분류 (살릴것 / 버릴것)
| strategy | n(closed) | avg_R | MFE-capture | 진단 | 분류 |
|---|---|---|---|---|---|
| micro_reversion | 393 | −0.046 | 28% | okx 즉시역행 tail + capital 대칭; 본체는 near-BE | **실행수정→살림** |
| flow_pressure | 1232 | −0.352 | 19% | <5s 진입오판(fix前) + winner giveback; long-only(SPOT) | **실행수정→살림**(2모드 다해당) |
| burst_rider | 92 | −0.075 | **4%** | winner +1.98R MFE를 +0.14R로; fee | **실행수정→살림**(let-run trail 최우선) |
| fx_range_fade | 4 | **+0.251** | 41% | 이미 fade-exit 받음; fee가 GOOD신호 살해 | **살림**(maker진입만 추가) |
| xau_indices_trend | 23 | −0.002 | 1% | mae −0.14R tight, 가장 healthy; fee-only 누수 | **살림**(maker진입) |
| session_breakout | 63 | −0.019 | 1% | 가격+$2를 fee $165가 역전; winner +0.77R→+0.009R | **살림**(maker+let-run) |
| fx_breakout_basket | 30 | 0.0 | 0% | Donchian-40 가격~flat; 30x fee churn | **경계**(엣지 얇음, maker로 재평가) |
| equity_gap_go | 16 | −0.217 | — | 마이크로캡 갭 슬리피지(max644bps)+엑싯늦음+6%승 | **버림/유니버스재설계** (진입엣지 약) |
| equity_tsmom | 37 | −0.086 | — | 6809신호→37fill, Alpaca 최대$출혈 −$773 | **유니버스 품질문제**(신호로직 sound, 페니주 universe) |
| tsmom (okx) | 2 | −0.116 | — | dormant pre-reset | **dormant**(판정보류) |
| volume_burst | 1 | −1.213 | — | dormant 단일표본 | **dormant**(판정보류) |

## 3대 실행 수정 (전부 flow_not_block — 사이즈컷/블록 0)
1. **let-winners-run trail** (momentum/breakout family): peak-분율 lock으로 burst_rider/session_breakout winner를 흐르게. give-back 60% harvest가 +7R→+0.001R 살해 중.
2. **maker/limit 진입 라우팅** (Capital+OKX): taker→maker로 fee/slippage 잠식 제거. fx_range_fade·xau·session을 가격-양 위에서 net-양으로 단번에 뒤집는 **최고 레버 단일수정**.
3. **진입 follow-through 확인틱 + tight-spread 심볼 한정** (micro_reversion okx / equity_gap_go): flow_confirmed 패턴 확장. veto 아닌 timing 정밀.

## 게이트 감사 연결 (감사가 밝힌 G5/G7/G8 — 데이터로 교차확인)
- **G8 broken**: learner `triple_stats` 155키 전부 value=1.0 고정(n_eff 278까지 쌓였는데 미갱신) → 나쁜/null 학습이 G5로 전파.
- **G5 size-cut**: `regime_mult` flow_pressure:chop=0.3×(n1086) + session_mult 0.3× 스택 → 최다거래 전략에 깊은 사이즈컷. (단 사이즈컷은 손익을 R로 normalize하므로 avg_R 진단엔 중립; $ 절대수익엔 악영향.)
- **G7 즉시청산**: <2s exit 411건이 모드1과 겹침 — atr_trail/scalp_stop이 스프레드 內 즉발. 단 <2s flow_pressure는 mae −1.88R(진입자체 역행)이라 G7만의 문제 아님 = 진입+엑싯 복합.

**/debate 가치**: ① let-winners-run trail 파라미터(peak-lock 분율) ② maker-limit 진입 라우팅(체결률 vs fee 트레이드오프) ③ tsmom/volume_burst dormant 재활성 vs 폐기 — 전부 전략-거동 변경 → /debate 트리거.
