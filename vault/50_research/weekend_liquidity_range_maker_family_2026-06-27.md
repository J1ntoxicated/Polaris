---
type: research
status: design
date_created: 2026-06-27
date_updated: 2026-06-27
tags: [research, strategy, weekend, mean-reversion, maker, okx-spot, fee, crypto-microstructure, flow-not-block]
---

# weekend_liquidity_range — 주말 저유동성 maker-reversion 패밀리 (OKX SPOT long-only)

DEMO/PAPER · aggressive 보존 · flow_not_block(차단/사이즈컷/진입블록 아님) · -1.0R rail · let-run.
backlink: [[strategy_vs_execution_partA_2026-06-24]] · [[all_strategy_edge_diagnosis_2026-06-24]]

## 핵심 발견 (load-bearing): maker가 fee 수학을 뒤집는다 — 정량
기존 리서치는 "인트라데이 크립토 = fee-fatal" 결론을 **taker(시장가, 스프레드 크로스)** 가정 위에 세웠다.
maker(지정가, 내가 호가) 실행이면 스프레드를 회피 → round-trip 비용이 **스프레드 무관 flat 16bps**.
OKX base: maker 8bps / taker 10bps per side.

| 주말 스프레드 | maker/maker RT | taker/taker RT | maker 절감 |
|---|---|---|---|
| 4bps (메이저) | 16bps | 24bps | 8bps |
| 8bps | 16bps | 28bps | 12bps |
| 15bps (알트) | 16bps | 35bps | 19bps |
| 25bps | 16bps | 45bps | 29bps |
| 40bps (thin알트) | 16bps | 60bps | 44bps |

핵심: 주말에 책이 얇아질수록(스프레드↑) maker 우위가 **벌어진다**. taker는 thin-book에서 자멸, maker는 16bps 고정.
[[strategy_vs_execution_partA_2026-06-24]] 측정 OKX 틱 슬리피지 11.24bps와 정합 — 그게 taker가 크로스한 스프레드.

## 크립토-네이티브 근거 (게싱 X, 출처 기반)
- **주말 유동성**: 거래량 주말 20-25%↓ → orderbook 얇음, 스프레드 확대(주말효과 momentum 논문 + Glassnode orderbook 메트릭).
- **주말 효과 알트>메이저**: 주말 수익 알트 0.0041 vs 평일 0.0019 (DOGE 0.0052/SOL 0.0048), 메이저 0.0026 vs 0.0014. **알트가 주말 mean-revert 진폭이 큼.**
- **thin-book 스파이크 revert**: 토요일 청산 캐스케이드 down-spike가 "유동성 복귀 시 fade" (월요일 reopen). Saturday liquidation → 과매도 dip이 revert.
- **펀딩레이트 = spot 신호(거래 X)**: 음(-) 펀딩 = 숏 과밀 = 과매도 contrarian long 신호. 2026 BTC perp 펀딩 최장 음수 streak(2022 바닥 이후) → relief rally 선행 패턴. SPOT long-only라 펀딩은 진입 confirm 신호로만.

## 기존 daily 돌파(turtle/donchian)와 차별 (명확)
- okx_donchian_55 / bar_breakout_run / turtle = **1D 종가 채널 돌파, TREND, 추세추종, hold_overnight 다일 swing**. 신호 빈도 낮음(채널 돌파 드묾).
- weekend_liquidity_range = **주말 한정, REVERSION(과매도 dip 매수), 분~시간 호라이즌, maker 지정가, 책 얇을 때 빈발**. 돌파의 반대 방향(돌파=상단 추격 / 이건 하단 dip 매수).
- 시간축 충돌 없음: 돌파는 평일 24/7 추세, 이건 주말 thin-book reversion 윈도우.

## 후보 3종 (StructuredOutput 참조)
1. **weekend_dip_maker_revert** — 주말 과매도 dip을 VWAP-이격 하단 maker 지정가 매수, 평균회귀 harvest.
2. **weekend_liquidation_fade** — 토/일 청산 down-spike(급락봉)를 maker 지정가로 fade, reopen revert.
3. **funding_oversold_maker_accumulate** — 음(-)펀딩 과매도 알트를 주말 thin-book서 maker 사다리 매집, 펀딩 정상화 revert.

## fill-risk (정직)
maker = 지정가 미체결 위험. 빠른 V자 회복은 지정가 아래 안 와서 놓침. 완화: (a) 사다리 다단 지정가(일부만 체결돼도 OK), (b) thin-book이라 호가 근처 자주 터치(체결률↑ vs 평일), (c) TTL 후 미체결 취소 = 0 비용(taker 손실 없음). 놓친 fill = 기회손실이지 실현손실 X — 비대칭 유리.

## 미해결 (배포 전 검증 의무)
- OKX SPOT maker 지정가 post-only 주문 지원 + demo 체결 시뮬 정확도 (executing-orders 어댑터 확인).
- 주말 스프레드 실측(현재 11.24bps는 전체평균, 주말/평일 분리 안됨) → tick_inflow 데이터로 주말 코호트 분리 측정.
- /debate 트리거: maker 지정가 위치(이격 분율) + 체결률 vs fee 트레이드오프 = 전략-거동 변경.

## 백테스트 검증 결과 (2026-06-27) — candidate #2 weekend_liquidation_fade = REJECT
실데이터(yfinance 1h, 10 OKX 메이저/알트, 2024-11~2026-06, ~86주). maker fee 8bps/side·NO 스프레드 크로스 + 보수적 maker fill(adverse selection: limit 관통해야 체결, V-recovery 미체결) + taker 베이스라인(10bps+주말 스프레드 크로스) + 시간순 OOS 반분할 + 주말/평일 분리. 스크립트 `/tmp/weekend_fade_backtest.py`, `/tmp/spike_signal_probe.py`, `/tmp/sensitivity_probe.py`.

**핵심 반증 — maker 가설은 fee 수학은 맞으나 신호에 엣지가 없다:**
- 주말 maker net/trade **−0.487R** (OOS −0.422R, n=277), win 34% · taker **−0.373R** (OOS −0.261R).
- maker가 taker를 **뒤집지 못함** (delta −0.114R) — maker가 오히려 더 나쁨. fee는 16 vs 45bps로 절감했으나 −0.49R 신호 결손 앞에 무의미.
- **이유 = adverse selection이 신호를 거꾸로 선택:** maker bid는 가격이 계속 떨어져 관통할 때만(=continuation, stop 맞는 나쁜 케이스) 체결되고, 샤프한 V-recovery(좋은 케이스)는 resting bid 아래로 안 와서 놓침. → maker는 worse-selected subset만 잡음. fee가 binding constraint가 아니었다.
- **RAW 신호 probe (실행/비용 0):** 주말 down-spike 8바 forward close drift **mean −0.046 ATR**(≈flat), MFE +2.16 vs MAE −2.48(다운 excursion이 더 큼), positive-drift 54%/net-favorable 52%(코인플립). 청산 down-spike는 평균회귀 X — thin-book에서 더 흐른다(continuation).
- **민감도 그리드 전멸:** generous-fill(adverse selection 제거) + spike 1.3~3.0ATR + retrace 0.30~0.62 + hold 4~16 + stop 1.0~2.0ATR **전 조합 net-negative**. 강한 spike일수록 **더 나쁨**(−0.44→−0.55R; 큰 캐스케이드는 더 cascade). stop 2.0ATR가 −0.325R로 가장 덜 나쁘나(win↑) 여전히 음수 = slow bleed.
- 주말 cohort가 평일(−0.126 ATR drift, maker −0.549R)보다 **덜 나쁨** = 주말 윈도우 가설 방향은 맞으나 "덜 나쁨"≠"양수".

**결론:** maker 실행이 fee를 이긴다는 명제 자체는 참(16bps flat). 그러나 weekend liquidation-fade **진입 신호에 reversion 엣지가 없어** maker로도 음수. fill-risk가 치명이 아니라 **신호가 치명** → REJECT (taker도 무의미하므로 TAKER_ONLY 아님). 패밀리 candidate #1(weekend_dip_maker_revert, VWAP-이격)/#3(funding_oversold)는 다른 진입신호라 별도 검증 필요 — fade-the-spike 형태는 폐기.

---
## candidate #1 weekend_dip_maker_revert 백테스트 (2026-06-27) = REJECT
별도 파일: [[weekend_dip_maker_revert_backtest_2026-06-27]]. 요약: maker fee 뒤집힘·주말효과 real 확인했으나 신호 진폭(frictionless 4-14bps)<maker cost 16bps → REJECT. candidate #2 fade와 동일 근본(adverse selection이 엣지 tail 선택해버림) 독립 수렴.

---
## candidate #4 weekend_volatility_cycle 백테스트 (2026-06-27) = REJECT (0 deployable)
별도 angle: vol-레짐 타이밍(가격극단 진입 아님 → adverse-selection 회피 의도). 실데이터 yfinance 1H 12심볼 723~728일(~104주말). 스크립트 `/tmp/weekend_volcycle_probe.py`(raw frictionless 4-thesis) · `/tmp/sunday_recharge_bt.py`(stage2) · `/tmp/sunday_exit_sweep.py`(exit grid).

**4 vol-cycle 가설 전부 raw-probe부터 탈락:**
- **H1 compression→expansion** (주말 저변동 vol 압축 → 월요일 확장 라이드): 주말 low-rv fwd drift **음수** 전 horizon(−0.018~−0.53 ATR), pos-drift 48~50%. 평일 low-rv는 약양수. 압축이 long-favorable 확장을 예측 안함 → KILL.
- **H4 weekend compressed breakout** (압축베이스서 레인지 상향돌파 모멘텀): break_up drift ~flat→음수(−0.18~−0.39 ATR @12-24h), mfe+mae +0.16→음수. 주말 돌파는 persist 안하고 fade. control(임의 주말바)이 오히려 나음 → KILL.
- **H2/H3 Sunday-night recharge** (Sun 20-23 UTC 월요일 reopen 전 long): **유일 raw 시그널** — pos-drift 55~57%, **MEDIAN +0.37~0.62 ATR** 전 horizon(n=4944). 단 MEAN은 약음수 = fat left-tail(일요일 down-shock) 비대칭 분포.

**stage2 + exit-sweep로 H3 정밀 검증 — 8bps gross 벽:**
- bounded target(+0.4 ATR)+−1.0R rail: win 57%, med-net +4bps이나 **gross −4.5bps**(rail tail이 median win 잠식). trend+notdump 필터(close>ma50 & 진입바 非급락)로 개선해도 alt class win 63.5%/PF 0.40이나 여전히 gross-음수.
- **exit grid 8종 sweep**(target 0.4~1.0 / rail 0.7~1.0 / let-run trail): 최고 frictionless **GROSS = +8.31bps**(letrun arm0.5 lock0.6 rail1.0, fat MFE 우측꼬리 수확). gross 양수 도달은 했으나 **maker RT 16bps에 못 미침** → net −7.69bps, PF 0.78. **전 config net-음수.**
- **binding constraint = maker fee**(신호 부재 아님 — 직전 dip/fade와 대조). Sunday recharge는 real 8bps gross edge이나 16bps maker floor가 삼킴 = [[weekend_dip_maker_revert_backtest_2026-06-27]] "amplitude(4-14bps)<cost(16bps)" 와 **동일 벽에 독립 수렴**.

**결론:** weekend_volatility_cycle 4가설 전부 net-음수 → **0 deployable candidate** (정직한 0). vol-압축/돌파는 raw drift 자체가 없고, Sunday-recharge는 raw 8bps gross가 maker 16bps에 미달. weekend_thin_book_flush(이미 배포, +73bps)가 여전히 유일 검증 주말 엣지. 교훈 재확인: 주말 엣지는 **vol-레짐 타이밍이 아니라 thin-book 마이크로구조 익스트림(flush)** 에서만 나왔고, 그 외 주말 directional 시그널은 8bps 이하 = maker floor 미달. 거동변경/빌드 불필요.
