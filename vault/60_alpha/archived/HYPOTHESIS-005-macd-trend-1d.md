---
entity_type: hypothesis
entity_id: HYPO-005
auto: false
last_modified: 2026-05-04
expires: 2026-09-04
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-015]]", "[[INSIGHT-007]]"]
mode: alpha
reviewed_by: codex
maturity: archived
tags: [type/hypothesis, status/archived, scope/alpha, polaris]
---

# HYPO-005 — MACD Trend 1d (archived)

## Hypothesis
MACD(12, 26, 9) BTC 1d crossover trend = expectancy > fee.

## Result (BTC 1d 1500 candles)
| Config | trades | hit | expectancy | Sharpe |
|---|---|---|---|---|
| MACD(12, 26, 9) | 58 | ? | **+0.0009** (fast-fail) | +0.009 |
| MACD(8, 21, 5) | 88 | ? | **+0.0005** (fast-fail) | +0.006 |

→ **Fast-fail** (expectancy < fee 0.014).

## Why fail vs HYPO-003/004 viable

신호 빈도 비교 (BTC 1d, 8.5년):
- SMA(50, 200): **8 trades** → 평균 hold 1년 → 큰 trend → fee 통과
- Donchian(40/15): **26 trades** → 평균 hold 4개월 → 큰 trend → fee 통과
- MACD(12, 26, 9): **58 trades** (1500 candles) → 평균 hold ~1개월 → whipsaw → fee 잠식
- MACD(8, 21, 5): **88 trades** → 더 잦은 whipsaw

→ Pattern: **1d trend follower라도 신호 빈도가 fee × 2 통과 못 하는 평균 hold 길이면 archived**.

## INSIGHT 추출

→ [[INSIGHT-007]] (fee 함정) 보강: 1d timeframe도 신호 빈도가 너무 잦으면 (월 5+ trade) fee 잠식. **viable 1d trend = SMA(50, 200) 또는 Donchian(40/15) 같은 long-cycle 신호만**.

ADR-009 PERP counter는 변경 없음 (HYPO-003/004 viable 입증으로 SPOT 1d trend 구조 확인).

## Related
- INSIGHT-007 (fee 함정 1d 신호 빈도 적용)
- HYPO-003 (SMA crossover viable)
- HYPO-004 (Donchian viable)
