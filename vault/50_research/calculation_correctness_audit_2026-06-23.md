---
type: research
status: active
date_created: 2026-06-23
date_updated: 2026-06-23
tags: [research, audit, calculation, sizing, risk-usd, venue, measurement]
---

# Calculation-Correctness Audit — 3 venue × 자산군 (ULTRACODE 전수)

DEMO/PAPER, aggressive bias 유지. READ-ONLY 감사(코드편집 0). 게싱 0 — code file:line + `data/polaris_live.sqlite` first-principles 재계산 대조. 핵심 결론: **봇은 실제로 잘못 거래하지 않는다 — EXECUTION 영향 0건, 6건 전부 MEASUREMENT-only.** 백링크 [[structure_hardening_2026-06-23]] · [[ADR-003-8-layer-architecture]].

## 자산군 × 계산 정확성 맵
| 자산군 | notional/size_usd | risk_usd | pnl_usd | fee | R |
|---|---|---|---|---|---|
| OKX 크립토 SPOT (USDT≈USD) | 정확 | 정확(분모1) | 정확 | 정확 | stream-R 정확 |
| Capital USD-quote (EURUSD/AUDUSD/US100/XAUUSD/WTI) | 정확 | 정확(rate=1) | 정확 | 정확 | 정확 |
| Capital 非-USD-quote (USDJPY/USDCAD/J225/HK50/DE40) | entry 정확 | **WRONG ×rate** | 정확 | 정확 | 정확 |
| Capital CLOSE-leg (cold cache) | **WRONG ×10** | — | 정확 | **WRONG ×10** | — |
| Alpaca 미국주식 (USD-native) | 정확 | 정확 | 정확 | 정확 | 정확 |

핵심: USD-quote / USDT / USD-native = 전 계산 정확. 결함은 **Capital venue로 bounded** (DB 검증).

## 확정 버그 (6건, 전부 배수오차 DB 검증)
1. **G1 risk_usd 非-USD-quote quote→USD 미변환** — `risk_unit.py:167-189` `risk=entry_price×atr_pct×stop×base_qty`가 quote-ccy 단위로 남음. caller `_production_pipeline.py:529-533`이 divisor 미전달. DB: USDJPY `pos_39e07240` risk_usd=4034.35 vs 정답 $25.00 = **정확히 ×161.4(entry_price)**. 인플레 배수: JPY×161 / CAD×1.42 / HKD×7.84 / EUR×1.14. **MEASUREMENT** (mfe_r/mae_r 엑서션 텔레메트리=러너 학습 input만; sizing/gate/block 미접근, risk_usd는 sizing 이후 stamp).
2. **G2 Capital CLOSE-fill size_usd ×10 legacy fallback** — `fill_normalizer.py:206-212` contract_factor_usd None(cold cache)→`size×pip(10)×lev(1)`. root=`_production_capital_sizing.py:318-340` peek가 restart-hydrated position에서 cache miss→None. DB: **18/224(8.0%) close degenerate, 0/235 entry degenerate**(=sizing 정상). AUDUSD $71000 vs 진실~$4900. **MEASUREMENT** (close-leg notional/exposure + 대시보드 display만; pnl_usd은 ENTRY size_usd로 계산되어 무관).
3. **G3 CLOSE fee_usd = G2의 ×10 size_usd의 3bps** (`fill_normalizer.py:216-217`) — 순수 downstream. DB exact: AUDUSD $71000→$21.30. **MEASUREMENT**, G2 고치면 자동 소멸. severity low.
4. **G6 legacy pnl_r ATR-분모 오염** (closed_ts≤1782166026) — DATA only. DB: OKX 351 + Capital 112 legacy행 |pnl_r|≤15.62 vs stream-era(560/109행) ≤0.018. 현재코드는 `close_helpers.py:149` realised_r_stream(R_budget)로 정확. **MEASUREMENT** (러너/bleeder 학습 input만). 마이그레이션만, 코드 변경 0.
5. **G4 OKX exact-dup close-fill 행** — 동일 instrument+ts_ms+base_qty 2행. DB(ADA `...566587`): 2행 base_qty SUM 중복 BUT pnl=`-0.9618, 0.0`(frac clamp `close_helpers.py:160-165` 검증). raw base_qty SUM 소비처만 과대. **MEASUREMENT/display**.
6. **G5 OKX partial-close 잔량 dust** — SPOT available shortfall→close<entry qty, position OPEN 유지(flow_not_block). 청산분 pnl_usd 정확. position 완결성만. **MEASUREMENT**.

## SYSTEMIC quote→USD (Capital 한정, 2갈래)
(A) **TRUE math 누락** = #1 risk_unit.py: rate(`constraint_translator`가 contract_factor_usd로 해석)가 entry size_usd엔 적용되나 risk_usd_at_entry엔 미전달 → 非-USD-quote 결정적 인플레. (B) **cache-warmth 누락** = #2 close peek-cache cold→×10(quote 무관, USD-quote AUDUSD도 cold면 발현). OKX/Alpaca 무관 확정. seed의 "非-USD-quote 전반 의심"은 Capital로 정확히 bounded(DB 검증).

## Fix 플랜 (우선순위)
1. `risk_unit.py` risk_usd_at_entry에 quote→USD divisor 1회 분할 (검증: USDJPY $25.00 / USDCAD $24.50 / HK50 $0.60). caller `_production_pipeline.py:529` divisor 전달.
2. entry 시 contract_factor_usd를 position에 영속화 → close가 cache 무의존 read (`_production_capital_sizing.py:318` peek 의존 제거).
3. (자동) RANK2 후 fee 검증만.
4. legacy 행(closed_ts≤1782166026) pnl_r 일회성 backfill(=pnl_usd/R_budget) 또는 학습에서 age-out. 코드 0.
5. close-poll exact-dup dedupe(`_production_close.py` persist). 6. dust 잔량 sweep 또는 documented fee-dust 수용(over-sell 금지 유지).

전 신규 BUILD = fresh Claude sub-agent 리뷰 의무. 불변 OK: DEMO/PAPER · aggressive · flow_not_block · 9-stack 봉쇄 · GPT=0 · Anthropic 개발용만. rejection-keyword sweep 0.
