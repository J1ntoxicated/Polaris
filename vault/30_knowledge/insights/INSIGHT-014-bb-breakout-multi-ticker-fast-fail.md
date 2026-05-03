---
entity_type: insight
entity_id: INSIGHT-014
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INSIGHT-007]]", "[[INSIGHT-013]]", "[[ADR-009]]", "[[60_alpha/_README]]"]
mode: alpha
reviewed_by: codex
maturity: authoritative
authoritative_basis: 직접 백테스트 (3000 candles BTC 1h/4h × 6 BB params + ETH-USDT 1h cross-check)
tags: [type/insight, status/active, scope/alpha, priority/p0, polaris]
---

# INSIGHT-014 — BB Breakout Multi-ticker fast-fail (HYPO-002)

> [[INSIGHT-013]] (RSI mean reversion fast-fail) 후속 측정. BB breakout (momentum) + 다중 ticker도 동일 fast-fail. SPOT 1.4% fee 환경 = mean-reversion 또는 momentum 모두 net negative 패턴 확인.

## Evidence

### BTC-USDT 1h, 6 BB 파라미터
| BB config | n_trades | hit_rate | expectancy | Sharpe | MDD |
|---|---|---|---|---|---|
| BB(20, 2.0) | 49 | 24.49% | -0.01247 | -0.791 | 46.24% |
| BB(20, 2.5) | 38 | 21.05% | -0.01371 | -0.971 | 41.03% |
| BB(20, 1.5) | 63 | 23.81% | -0.01184 | -0.712 | 53.20% |
| BB(10, 2.0) | 64 | 17.19% | -0.01256 | -0.852 | 55.77% |
| BB(50, 2.0) | 26 | 11.54% | -0.01732 | -0.971 | 38.69% |
| BB(20, 3.0) | 17 | 17.65% | -0.01569 | -1.026 | 23.72% |

### BTC-USDT 4h
| BB config | n_trades | hit_rate | expectancy | Sharpe | MDD |
|---|---|---|---|---|---|
| BB(20, 2.0) | 60 | 18.33% | -0.02200 | -0.764 | **74.54%** |
| BB(20, 2.5) | 37 | 10.81% | -0.02257 | -0.725 | 57.80% |
| BB(10, 2.0) | 78 | 16.67% | -0.01620 | -0.797 | 72.47% |
| BB(50, 2.0) | 23 | 30.43% | -0.01022 | -0.307 | 24.31% |

### ETH-USDT 1h cross-check
| Strategy | n_trades | hit_rate | expectancy | Sharpe |
|---|---|---|---|---|
| BB(20, 2.0) | 50 | 18.00% | -0.01118 | -0.452 |
| RSI(14, 30, 70) | 13 | 61.54% | -0.02206 | -0.231 |

## Pattern Confirmation

**모든 시도 expectancy < 0 (fee 1.4% 통과 못 함)**:
- Mean reversion (RSI) — fast-fail (INSIGHT-013)
- Momentum (BB breakout) — fast-fail (이 INSIGHT)
- 다른 ticker (ETH) — 동일 결과
- 다른 timeframe (1h/4h) — 동일

→ **SPOT 1.4% fee 환경에서 단순 technical alpha = 수학적 net negative** ([[INSIGHT-007]] 정합).

## Why momentum 더 나쁨?

BB breakout 1h: hit_rate 11-25% (RSI mean reversion 39-50%보다 낮음). 이유:
- Breakout 신호 후 false breakout 빈번 (whipsaw)
- BTC 1h trending이 명확하지 않음 (mean reversion이 momentum보다 약간 나음)
- 단 둘 다 fee 1.4% 통과 못 함

## Polaris 적용

### HYPO-002 archived
- 모든 파라미터 + ticker + timeframe fast-fail
- ADR-009 PERP counter +1 → **2/5**

### Pattern shift 시그널
- 단순 technical (price/volume only) alpha = SPOT fee 1.4% 통과 어려움
- 다른 alpha 카테고리 시도 필요:
  - Funding rate arbitrage (INSIGHT-005 참고)
  - On-chain (active addresses, exchange flow)
  - Sentiment (Fear/Greed regime)
  - Cross-asset (BTC/ETH ratio mean reversion)
  - Macro (DXY/VIX 정합)

### 또는 PERP 검토 즉시 트리거
- 5/5 도달 안 기다리고 2/5에서 ADR-009 검토 가능
- 모태 ADR-011 같은 데이터 누적 (228+ closed trades)는 paper 운영 필요
- 백테스트 만으로 PERP 결정 어려움 (INSIGHT-012 신뢰도 한계)
- 권장: HYPO-003 (다른 카테고리 1개) 시도 후 결정

## Recommendation
- [x] HYPO-002 archived
- [x] PERP counter 2/5 update
- [ ] HYPO-003 후보: Funding rate carry 또는 BTC/ETH ratio
- [ ] PERP 검토는 3-4/5 도달 시 ADR-009 update 권장 (5 wait 너무 길음)

## Related
- INSIGHT-007 (OKX SPOT fee 수학)
- INSIGHT-012 (백테스트 신뢰도 한계)
- INSIGHT-013 (RSI fast-fail)
- ADR-009 (SPOT vs PERP)
- ADR-010 (Backtest + Paper parallel)
- 60_alpha/_README
