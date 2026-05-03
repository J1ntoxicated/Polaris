---
entity_type: adr
entity_id: ADR-011
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INSIGHT-015]]", "[[ADR-010]]", "[[60_alpha/_README]]", "[[HYPO-003|60_alpha/active/HYPOTHESIS-003-sma-crossover-1d]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: provisional
tags: [type/adr, status/provisional, scope/alpha, priority/p1, polaris]
---

# ADR-011 — Promotion Gate Timeframe-aware (1h scalp vs 1d trend 분리)

## Status
- proposed: 2026-05-03 (HYPO-003 1d 발견 후)
- provisional: 2026-05-03

## Context

기존 Promotion Gate (ADR-010):
- Sharpe ≥ 0.5
- win_rate ≥ 0.52
- MDD ≤ 0.10

**문제 발견 ([[INSIGHT-015]])**: 1d trend following BTC 8.5년 backtest 모두 fast-fail 통과 (expectancy 양수)이지만 위 Gate 미달:
- Sharpe 0.15-0.48 (trend 빈도 낮아 mean/std 비율 낮음)
- win_rate 30-42% (trend = 큰 winner + many small loss)
- MDD 36-67% (1d crypto 본질)

→ Gate가 1h scalping 가정에 묶여 1d trend following 차단. 카테고리별 적정 기준 필요.

## Decision

**Timeframe-aware Promotion Gate** — strategy timeframe + category에 따라 기준 분리.

### Category 1: Scalp / Short-hold (1m, 5m, 15m, 1h)
- n_trades ≥ 50
- Sharpe ≥ 0.8 (trade 빈도 높음 → mean/std 안정)
- win_rate ≥ 0.55
- MDD ≤ 0.10
- expectancy > fee × 1.5

### Category 2: Swing / Mid-hold (4h, 12h, 1d)
- n_trades ≥ 15 (trade 빈도 낮음)
- Sharpe ≥ 0.3
- win_rate ≥ 0.30 (trend 본질)
- MDD ≤ 0.50 (crypto 본질)
- expectancy > fee × 5 (= 0.07)

### Category 3: Position / Long-hold (3d+, weekly)
- n_trades ≥ 8
- Sharpe ≥ 0.2
- win_rate ≥ 0.25
- MDD ≤ 0.70
- expectancy > fee × 10 (= 0.14)

### Universal (모든 category)
- Fast-fail gate: expectancy > fee_round_trip
- Multi-ticker 검증 (BTC 외 ≥ 2 ticker 동일 패턴)
- Paper 30-180일 (category 따라)

## Code 변경 (Phase 2)

`src/backtest/promotion_gate.py` 함수 분리:
- `evaluate_promotion_scalp(result)` — Category 1
- `evaluate_promotion_swing(result)` — Category 2
- `evaluate_promotion_position(result)` — Category 3
- `evaluate_promotion(result, category=...)` — dispatcher

## Consequences

### 긍정
- HYPO-003 1d trend following = Category 2 Gate에 fit
- 1h scalp는 여전히 엄격 (INSIGHT-007 fee 함정)
- 카테고리별 paper period 차별화 (1h 30일 vs 1d 90-180일)

### 부정
- Gate 복잡도 증가 (1 → 3 카테고리)
- Category 분류 모호 (4h = scalp or swing?)

### Mitigations
- 4h 경계는 Sharpe/n_trades 따라 자동 분류 (n_trades > 100 → scalp, ≤ → swing)
- 새 strategy 추가 시 ADR로 category 명시

## Verification
- [ ] promotion_gate.py 3 함수 분리
- [ ] HYPO-003 Category 2 Gate 평가 (pass 예상)
- [ ] HYPO-001/002 Category 1 Gate 평가 (fast-fail 그대로)
- [ ] codex 외부 리뷰 (ADR-004)

## Rollback Path
- Category 분류가 운영 중 confusion 유발 시 → 단일 Gate 복귀 + Sharpe 0.3으로 완화 (별도 ADR)

## Related
- INSIGHT-015 (1d viable 발견)
- ADR-010 (Backtest + Paper parallel)
- ADR-009 (SPOT vs PERP — 1d viable로 PERP 약화)
- 60_alpha/_README
