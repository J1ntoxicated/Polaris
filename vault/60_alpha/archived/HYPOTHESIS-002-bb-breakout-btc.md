---
entity_type: hypothesis
entity_id: HYPO-002
auto: false
last_modified: 2026-05-03
expires: 2026-09-03
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-014]]", "[[INSIGHT-007]]"]
mode: alpha
reviewed_by: codex
maturity: archived
tags: [type/hypothesis, status/archived, scope/alpha, polaris]
---

# HYPO-002 — BTC-USDT BB Breakout (momentum)

> RSI mean reversion (HYPO-001) fast-fail 후 momentum 시도. 동일 fast-fail.

## Hypothesis

**H₁**: BB(20, 2.0) breakout (close > upper band ENTER_LONG, close < middle EXIT) 가 SPOT fee 1.4% 통과.

**H₀**: expectancy < fee_round_trip.

## Method

- BTC-USDT 1h, 4h
- BB 파라미터 sweep: period 10/20/50, std_dev 1.5/2.0/2.5/3.0
- ETH-USDT 1h cross-check
- 3000 candles per ticker

## Results

**모든 시도 fast-fail**. expectancy 모두 음수 (-0.01 ~ -0.02). MDD 24-74%. Hit rate 11-30%.

자세히는 [[INSIGHT-014]].

## Promotion 결정

**Archived 2026-05-03**. ADR-009 PERP counter 2/5.

## Related

- INSIGHT-014 (다중 ticker fast-fail 패턴)
- ADR-009 (SPOT vs PERP)
- HYPO-001 (선행 RSI 실패)
