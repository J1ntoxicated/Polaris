---
entity_type: hypothesis
entity_id: HYPO-006
auto: false
last_modified: 2026-05-04
expires: 2026-09-04
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-015]]", "[[HYPO-003|60_alpha/active/HYPOTHESIS-003-sma-crossover-1d]]"]
mode: alpha
reviewed_by: codex
maturity: archived
tags: [type/hypothesis, status/archived, scope/alpha, polaris]
---

# HYPO-006 — Ichimoku Tenkan/Kijun (simplified) 1d (archived)

## Hypothesis
Ichimoku Tenkan(9 mid)/Kijun(26 mid) crossover BTC 1d = expectancy > fee + Sharpe > 0.3 (swing).

## Result (BTC 1d 3000 candles)
| Config | trades | hit | exp | Sharpe | MDD |
|---|---|---|---|---|---|
| Ichimoku(9, 26) | 67 | 30% | +5.4% | 0.17 | 64% |
| Ichimoku(7, 22) | 80 | 31% | +3.9% | 0.14 | 74% |
| Ichimoku(12, 30) | 57 | 39% | +6.1% | 0.18 | 68% |
| Ichimoku(20, 60) | 28 | 43% | +20.3% | 0.26 | 46% |

→ **모든 시도 Sharpe < 0.3 (swing min)** + 일부 MDD > 50% → fast-fail 통과해도 Promotion Gate FAIL.

## Why fail vs HYPO-003 SMA(50, 200)

Ichimoku Tenkan/Kijun = mid price ((H+L)/2) crossover. SMA crossover = close 평균 crossover. 두 신호 거의 동일 메커니즘:
- Ichimoku(20, 60) ≈ SMA(20, 60) — 약간 다른 입력값
- HYPO-003 SMA(50, 200) = 더 긴 cycle = 더 적은 신호 = fee 통과 좋음
- HYPO-006 Ichimoku 짧은 cycle (9/26) = 더 잦은 신호 = whipsaw

→ HYPO-005 MACD와 같은 pattern: 1d trend도 신호 빈도 ≥ swing 임계값이면 fee/Sharpe 미달.

## Polaris 적용

- Archived (added value 없음 — SMA crossover 변형)
- Pattern 강화 ([[INSIGHT-007]] timeframe + 신호 빈도 fee 함정)
- HYPO-007+ 후보: 다른 메커니즘 (cross-asset, volume burst, on-chain regime)

## Related
- INSIGHT-015 (1d viable)
- INSIGHT-007 (fee 함정)
- HYPO-003 (SMA crossover)
- HYPO-005 archived (MACD whipsaw 패턴)
