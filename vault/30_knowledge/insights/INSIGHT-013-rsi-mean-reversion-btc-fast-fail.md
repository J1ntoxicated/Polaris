---
entity_type: insight
entity_id: INSIGHT-013
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INSIGHT-007]]", "[[ADR-009]]", "[[HYPO-001|60_alpha/active/HYPOTHESIS-001-rsi-mean-reversion-btc-1h]]", "[[60_alpha/_README]]"]
mode: alpha
reviewed_by: codex
maturity: authoritative
authoritative_basis: 직접 백테스트 측정 (3000 candles BTC 1h × 6 RSI 파라미터 + 4 타임프레임)
tags: [type/insight, status/active, scope/alpha, priority/p0, polaris]
---

# INSIGHT-013 — RSI Mean Reversion BTC fast-fail (HYPO-001 첫 측정)

> [[INSIGHT-007]] (OKX SPOT fee 수학적 불가능) 직접 백테스트 검증 — RSI mean reversion BTC 모든 파라미터 fast-fail. ADR-009 PERP counter +1.

## Evidence (직접 측정)

### 데이터셋
- BTC-USDT 1h, OKX `/api/v5/market/history-candles` 3000 candles (~125일)
- Range: 2026-01 ~ 2026-05-03
- Fee: 0.014 round-trip (INSIGHT-007 정합)

### Parameter Matrix (1h)
| RSI config | n_trades | hit_rate | expectancy | Sharpe | MDD |
|---|---|---|---|---|---|
| RSI(14, 30, 70) | 10 | 50.00% | -0.02248 | -0.231 | 30.48% |
| RSI(14, 35, 65) | 17 | 17.65% | -0.02682 | -0.512 | 39.76% |
| RSI(14, 40, 60) | 31 | 35.48% | -0.01546 | -0.433 | 39.66% |
| RSI(7, 30, 70) | 38 | 39.47% | -0.01498 | -0.433 | 44.95% |
| RSI(7, 25, 75) | 23 | 34.78% | -0.02090 | -0.382 | 42.80% |
| RSI(21, 30, 70) | 5 | 60.00% | -0.00996 | -0.063 | 26.13% |

### Timeframes (RSI 14, 30, 70 baseline)
| Timeframe | n_trades | hit_rate | expectancy | Sharpe |
|---|---|---|---|---|
| 5m | 4 | 0.00% | -0.01037 | -21.686 |
| 15m | 3 | 0.00% | -0.00962 | -0.774 |
| 1h | 10 | 50.00% | -0.02248 | -0.231 |
| 4h | 3 | 66.67% | -0.03608 | -0.238 |

## Conclusion

**모든 시도 fast-fail (expectancy < fee 0.014)**. INSIGHT-007 (OKX paper Lv1 fee 1.4% scalp 수학적 불가능)이 RSI mean reversion에 그대로 적용. 표준 + 변형 파라미터 모두 net negative.

### Root Cause
- BTC 1h timeframe에서 RSI 30 trigger 자체가 드뭄 (10 trades / 125일)
- 임계값 완화 (40/60) 시 trade 수 늘지만 hit_rate 떨어짐 (35%)
- 모든 trade의 평균 net이 fee 통과 못 함 (-1~-3% per trade)

### 사용자 의문 ("백테스트 얼마나 믿을수 있어?") 직접 검증
- n_trades 4-10 = 통계적 의미 매우 낮음 (INSIGHT-012 정합)
- 그러나 모든 시도가 일관되게 negative → 통계 신뢰도 낮아도 **방향성은 명확**
- 백테스트 = "버그 탐지 + fast-fail gate" 가치 (ADR-010 정합) 검증

## Polaris 적용

### HYPO-001 archived (61_alpha/archived/)
- expectancy 모든 파라미터 음수
- Promotion Gate 모든 기준 fail
- INSIGHT-007 fee 함정 그대로 재현

### ADR-009 PERP counter
- SPOT-only 유지 + 5+ 연속 fast-fail 시 PERP 검토 ADR 트리거
- **현재 counter: 1/5** (HYPO-001)

### HYPO-002 후보 (다른 alpha 시도)
- Bollinger Band breakout (momentum, wider TP)
- Trend following (RSI 70 long entry, 30 exit — 반대)
- Funding rate arbitrage (정량 funding signal)
- Volume + price action (volatility_spike-like, INSIGHT-004)

## Recommendation
- [x] HYPO-001 archived (60_alpha/archived/)
- [ ] HYPO-002 작성: Bollinger Band breakout 또는 momentum
- [ ] ADR-009 PERP counter file 작성 (vault/50_runtime/perp_counter.md)
- [ ] 5+ counter 도달 시 PERP 검토 ADR 강제 트리거

## Related
- INSIGHT-007 (OKX SPOT fee 수학적 불가능)
- INSIGHT-012 (백테스트 신뢰도 한계)
- ADR-009 (SPOT vs PERP)
- ADR-010 (Backtest + Paper parallel)
- 60_alpha/_README
