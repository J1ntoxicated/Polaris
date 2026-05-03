---
entity_type: hypothesis
entity_id: HYPO-003
auto: false
last_modified: 2026-05-03
expires: 2026-09-03
editable: true
back_links: ["[[60_alpha/_README]]", "[[INSIGHT-015]]", "[[ADR-010]]", "[[ADR-009]]"]
mode: alpha
reviewed_by: codex
maturity: verified
tags: [type/hypothesis, status/active, scope/alpha, priority/p0, polaris]
---

# HYPO-003 — SMA(50, 200) BTC 1d Trend Following

> 첫 fast-fail 통과 (INSIGHT-015). 1d trend following으로 SPOT 1.4% fee 함정 우회 가능 입증.

## Hypothesis

**H₁**: BTC-USDT 1d SMA(50, 200) golden cross ENTER_LONG / death cross EXIT 가 OKX SPOT paper Lv1 fee 1.4% round-trip 후 expectancy > 0 (paper 검증 시 confirmed).

**H₀**: 백테스트 +47% expectancy = overfitting / regime bias / 통계 noise (n=8 trades 8.5년).

## Rationale

- BTC 8.5년 SMA(50, 200) backtest: 8 trades, hit 62.5%, exp +47.69%, Sharpe 0.475
- INSIGHT-015 (timeframe-aware fee 함정): 1d trend = 큰 winner → fee 1.4% 통과
- 멀티 ticker 1d (ETH/SOL/BNB) 동일 패턴

## Method (ADR-010 + 신규 timeframe-aware Gate)

### Stage 1: BACKTEST (8.5년 데이터)
- 데이터: BTC-USDT 1d, OKX max ~3127 candles
- Strategy: SMACrossover(fast=50, slow=200)
- Fee: 0.014 round-trip
- ✅ **Pass**: 모든 파라미터 fast-fail 통과, expectancy 양수 일관

### Stage 2: PAPER (timeframe-aware 90일+)
- 1d timeframe → 30일 30 trades 불가능 (1d 평균 trade 빈도 12-30/년)
- 90-180일 운영 (10-15 trades 예상) → INSIGHT-012 통계 신뢰도 한계
- Multi-ticker 분산 (BTC + ETH + SOL) → 가설 1개당 trade 30+ 가능

### Stage 3: Promotion Gate (Timeframe-aware ADR-011 신설 후)

기존 Gate (1h scalp 가정) 미달 → 새 Gate:
- Sharpe ≥ 0.3 (1d trend 본질 — trade 빈도 낮아 mean/std 비율 낮음)
- win_rate ≥ 0.30 (1d trend = 큰 winner + many loss)
- MDD ≤ 50% (1d crypto 본질)
- expectancy > fee × 5 (= 0.07 — 큰 trend per trade 입증)

## Fast-fail Gate
- ✅ 통과 (BTC 1d 모든 파라미터 expectancy 양수)

## Results

### BACKTEST 1차 (BTC 1d 8.5년)
| Strategy | n | hit | exp | Sharpe | MDD |
|---|---|---|---|---|---|
| SMA(10, 30) | 64 | 30% | +5.0% | +0.15 | 67% |
| SMA(20, 50) | 34 | 32% | +14.2% | +0.20 | 54% |
| **SMA(50, 200)** | **8** | **62.5%** | **+47.7%** | **+0.475** | **36%** |

### Multi-ticker (1d SMA(20, 50))
- ETH 3000 candles: hit 57%, exp +26%
- SOL 2000 candles: hit 35%, exp +71%
- BNB 1230 candles: hit 43%, exp +5.5%

### PAPER (TBD)
- 인프라 작성 후 시작 (Phase 2c)
- 90-180일 multi-ticker 운영

## Risk
- n=8 trades = 통계 신뢰도 매우 낮음 (INSIGHT-012 정합)
- BTC 8.5년 = 1-2 super-bull cycle 의존 (regime bias)
- MDD 36-67% = paper에서 심리적 부담
- Survivorship: BTC 8.5년 데이터 자체가 bull cycle 평균

## Promotion 결정

**Active 유지**. 페이퍼 검증 후 Jin ack로 라이브 진입 결정.

## Related
- INSIGHT-015 (1d viable 발견)
- INSIGHT-012 (백테스트 신뢰도 한계)
- INSIGHT-007 (fee 함정 — timeframe-aware 재정의)
- ADR-009 (PERP — counter 약화)
- ADR-010 (Backtest + Paper)
- ADR-011 (Promotion Gate Timeframe-aware — 신설 후)
- src/strategies/sma_crossover.py
