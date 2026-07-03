# Capital 지수 breakout-FADE (후보④) — backtest (REJECT)

> 2026-07-03 · revival P1 후보 심사 · DEMO/PAPER · yfinance 1h 실데이터

## Question
[[donchian_indices_global_generalization_2026-06-27]]의 "지수는 돌파 후
mean-revert" 발견의 역방향 — US500/US100/DE40 세션 내 돌파 실패 fade
(전일 고저 돌파 → K-bar 내 회귀 시 역진입)가 real-fee 기하에서 OOS net 양수인가?

## Method
- Proxy: ^GSPC/^NDX/^GDAXI 1h bars 720d (DB bars는 1H ~2개월뿐이라 불충분).
- Rule: 전일 H/L 돌파 pierce → K=3 bar 내 레벨 안쪽 재종가 = fade 진입(taker close).
  Stop = 돌파 후 극값 ± 0.15×ATR14 · Target = 전일 range midpoint · time stop 24 bar.
- Cost: Capital demo 실측 스프레드(quote_ticks: US500 0.82 / US100 0.62–1.37 /
  DE40 0.59–0.81 bps) 왕복 0.9–1.4bps + 슬리피지 스트레스 왕복 10/15/20bps.
- Fee-in-R 사전 산술: median stop distance **40.8bps** → fee-in-R = 스프레드만
  0.03R, +10bps 슬립 0.28R, +15bps 0.44R, +20bps 0.58R.
- OOS = 시간순 반분할 (split 2025-01-14). n = 609 (IS 305 / OOS 304).

## Result — REJECT
- **Primary spec pooled OOS: gross −0.107 R/trade, net(스프레드만) −0.135 R,
  net(+10bps) −0.410 R.** IS도 gross −0.061 R — 양쪽 half 모두 음수.
- Per-symbol OOS net(슬립0): US500 +0.029 / US100 −0.156 / DE40 −0.242.
  US500 양수는 IS 음수와 sign-flip → noise.
- **24개 변형 sweep** (pierce/close 돌파 × K∈{2,3,5} × buf∈{0.15,0.5}×ATR ×
  target mid/range): 최고 OOS net(슬립0) = +0.030 R (selection max) —
  **왕복 10bps 스트레스에서 24/24 전부 음수** (best −0.168 R).
- Raw forward edge (fade 방향, gross·무비용): pooled hz4/8/24/48 =
  −3.9/−2.2/+0.6/−13.5bps. IS↔OOS sign-flip 상존. DE40은 fade 방향 일관 음수
  (돌파가 오히려 지속) — 엔트리 원형 자체에 엣지 없음.

## Interpretation
Donchian REJECT 문서의 mean-revert는 **30일 돌파·일봉 지평**의 현상이며,
세션(전일 H/L)·시간봉 지평의 돌파 실패 fade로는 이식되지 않는다. 구조적으로
stop 기하가 ~41bps라 슬리피지 스트레스만으로 0.3–0.6R을 잠식 — gross ±0.03R
수준의 원형은 어떤 exit로도 구제 불가 (no-edge entry 원칙 재확인).

## Decision
- **REJECT** 후보④. 빌드 스펙 없음. 지수 fade는 세션-지평 룰 기반으론 폐기.
- 잔여 탐색 여지(별도 후보로만): 일봉 지평 mean-revert (Donchian 문서가 실증한
  바로 그 지평) — 단 이는 후보④ 스코프 밖.

## Files
- Scratch: scratchpad/fade_bt.py · fade_diag.py (분석용, 프로덕션 무접촉)
