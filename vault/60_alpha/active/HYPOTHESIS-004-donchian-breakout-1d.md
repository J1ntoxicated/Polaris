---
entity_type: hypothesis
entity_id: HYPO-004
auto: false
last_modified: 2026-05-04
expires: 2026-09-04
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-015]]", "[[ADR-010]]", "[[ADR-011]]"]
mode: alpha
reviewed_by: codex
maturity: verified
tags: [type/hypothesis, status/active, scope/alpha, polaris]
---

# HYPO-004 — Donchian Channel Breakout 1d (Turtle Trading 변형)

> Polaris 두 번째 viable strategy. HYPO-003 (SMA crossover) 외 trend following 카테고리 다양화.

## Hypothesis

**H₁**: Donchian(40-day high entry, 15-day low exit) BTC 1d 가 SPOT fee 1.4% round-trip 후 expectancy > 0 (paper 검증 시 confirmed).

**H₀**: backtest +18% = overfitting / regime bias / noise.

## Rationale

- Turtle Trading (Richard Dennis 1980s) — 검증된 trend following
- Donchian channel = N-day extreme breakout
- INSIGHT-015 (1d trend timeframe-aware) 정합
- HYPO-003 SMA(50, 200)와 다른 신호 메커니즘 → 알파 분산

## Method

### BACKTEST 1차 (BTC 1d 8.5년, codex look-ahead fix 적용)

| Donchian (entry/exit) | category | Result | trades | hit | exp | Sharpe | MDD |
|---|---|---|---|---|---|---|---|
| (20/10) | swing | FAIL | 44 | 36% | +10.6% | 0.22 | 42% |
| (55/20) | swing | FAIL | 19 | 58% | +22.7% | 0.32 | 53% |
| **(40/15)** | swing | **PASS ✅** | 26 | 46% | **+18.1%** | **0.313** | **41%** |
| (10/5) | swing | FAIL | 82 | 35% | +2.8% | 0.14 | 59% |
| (30/10) | swing | FAIL | 34 | 44% | +15.4% | 0.29 | 34% |

**Best: Donchian(40/15) BTC 1d** — Promotion Gate Swing PASS.

### Multi-ticker (Donchian 20/10 1d)
- **ETH-USDT PASS ✅**: 35 trades, hit 54%, exp +15%, Sharpe 0.33
- SOL-USDT FAIL: 27 trades, hit 44%, exp +38%, Sharpe 0.32 (Sharpe 약간 미달)

→ ETH는 Donchian(20/10), BTC는 Donchian(40/15)이 각자 best (ticker별 최적 파라미터 다름).

## Fast-fail Gate
✅ 통과 (모든 변형 expectancy 양수, INSIGHT-007 fee 우회)

## Promotion Gate (ADR-011 swing)
- **Donchian(40/15) BTC 1d**: PASS (모든 swing 기준 통과)
- Donchian(20/10) ETH 1d: PASS

## Polaris 적용

### 페이퍼 운영 시작 (cron 추가)
- BTC-USDT Donchian(40/15) 1d
- ETH-USDT Donchian(20/10) 1d
- 6-12개월 paper 운영 → ADR 승격 결정

### HYPO-003 vs HYPO-004 알파 다양화
- 다른 신호 메커니즘 (MA crossover vs channel breakout)
- 다른 Promotion Gate 카테고리 (Position vs Swing)
- 운영 시 신호 상관성 추적 (ADR 후속)

## Risk
- Donchian(40/15) BTC trades 26 / 8.5년 = 3 trades/년 — 통계 신뢰도 낮음
- regime bias 가능 (bull cycle 의존)
- 전략 간 상관성 시 분산 효과 약화

## Recommendation
- [ ] Cron HYPO-004 추가 (BTC 40/15, ETH 20/10)
- [ ] Walk-forward 검증 (HYPO-003 패턴)
- [ ] 6-12개월 paper 운영
- [ ] HYPO-003 + HYPO-004 신호 상관성 추적

## Related
- INSIGHT-015 (1d viable)
- ADR-010 (Backtest + Paper)
- ADR-011 (Promotion Gate Timeframe-aware)
- HYPO-003 (SMA crossover)
- src/strategies/donchian_breakout.py
