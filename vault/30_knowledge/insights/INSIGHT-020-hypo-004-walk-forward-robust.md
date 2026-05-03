---
entity_type: insight
entity_id: INSIGHT-020
auto: false
last_modified: 2026-05-04
expires: never
editable: true
back_links: ["[[HYPO-004|60_alpha/active/HYPOTHESIS-004-donchian-breakout-1d]]", "[[INSIGHT-016]]", "[[INSIGHT-019]]"]
mode: alpha
reviewed_by: codex
maturity: authoritative
authoritative_basis: 직접 walk-forward (TRAIN 5년 + TEST 3.5년) + 3-fold (3 다른 cycle)
tags: [type/insight, status/active, scope/alpha, priority/p0, polaris]
---

# INSIGHT-020 — HYPO-004 Donchian(40, 15) Walk-forward Robust

> HYPO-003 패턴 반복 — Donchian breakout도 walk-forward + 3-fold 모두 양수 일관, regime robust 입증.

## Method

- BTC-USDT 1d 3127 candles (2017-10 ~ 2026-05, 8.5년)
- Strategy: DonchianBreakout(entry_period=40, exit_period=15)
- Fee: 0.014 round-trip

## Walk-forward (60/40)
| Set | trades | hit | expectancy | Sharpe | MDD |
|---|---|---|---|---|---|
| TRAIN (5년) | 13 | 46.15% | +0.3108 | +0.392 | 39.03% |
| **TEST (3.5년)** | **13** | **46.15%** | **+0.0520** | **+0.294** | **25.29%** |

→ TEST out-of-sample **양수** (overfitting 위험 낮음).

## 3-fold (각 ~3년)
| Fold | trades | hit | expectancy | Sharpe |
|---|---|---|---|---|
| 1 (2017-2020 1차 bull) | 6 | 66.67% | +0.2611 | +0.601 |
| 2 (2020-2023 bull+bear) | 11 | 36.36% | +0.1999 | +0.239 |
| 3 (2023-2026 3차 bull) | 10 | 40.00% | +0.0642 | +0.330 |

→ **모든 fold expectancy + Sharpe 양수** (regime robust).

## Comparison HYPO-003 vs HYPO-004

| 항목 | HYPO-003 SMA(50, 200) | HYPO-004 Donchian(40, 15) |
|---|---|---|
| Trades / 8.5년 | 8 | 26 |
| 평균 hold | ~1년 | ~4개월 |
| Hit rate | 62.5% | 46% |
| Expectancy (per trade) | +47.7% | +18.1% |
| Sharpe | +0.475 | +0.313 |
| TEST out-of-sample | +23.4% | +5.2% |
| Category | Position | Swing |

→ HYPO-003은 적게/큰 winner, HYPO-004는 더 자주/중간 winner. 다른 trade 빈도 + 신호 메커니즘 = 알파 분산.

## Polaris 적용

### HYPO-004 robust 확정
- BACKTEST PASS + walk-forward + 3-fold robust
- 페이퍼 검증 단계 (cron 자동 실행 중)
- 6-12개월 paper actual ≥ backtest 50% 시 ADR 승격 후보

### Polaris 첫 viable 알파 portfolio
- HYPO-003 SMA(50, 200) BTC 1d Position
- HYPO-004-BTC Donchian(40/15) BTC 1d Swing
- HYPO-004-ETH Donchian(20/10) ETH 1d Swing
- 신호 상관성 추적 추후 (BTC 동일 ticker × 다른 strategy의 신호 timing)

## Recommendation
- [ ] 6-12개월 paper 운영 (HYPO-003 + HYPO-004)
- [ ] HYPO-003 vs HYPO-004 신호 상관성 추적 (vault 50_runtime monthly)
- [ ] 추가 alpha 카테고리 시도 (Ichimoku, Cross-asset, Volume 등)
- [ ] Codex Round 3 (잔여 gap 5: stop-loss/dedup/partial/short/sizing)

## Related
- INSIGHT-015 (1d viable 발견)
- INSIGHT-016 (HYPO-003 walk-forward)
- INSIGHT-019 (HYPO-004 alpha 다양화)
- HYPO-003 active
- HYPO-004 active
- ADR-010 (Backtest + Paper)
