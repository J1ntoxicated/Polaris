---
entity_type: insight
entity_id: INSIGHT-012
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[ADR-010]]", "[[60_alpha/_README]]", "[[INSIGHT-002]]"]
mode: forensic
reviewed_by: codex
maturity: authoritative
authoritative_basis: codex 정량 분석 (Bailey et al. 2014 backtest overfitting + Sharpe SE 공식)
tags: [type/insight, status/active, scope/alpha, priority/p0, polaris]
---

# INSIGHT-012 — Backtest Reliability Limits (90일/50 trades = 통계적 경계)

> 사용자 핵심 의문 "백테스트 얼마나 믿을 수 있는가?" codex 정량 분석.

## Evidence (정량)

### Sharpe 신뢰구간 (n=50 trades)
- SE = √(1 + S²/2) / √n
- 측정 Sharpe 1.5, n=50 → 95% CI [1.10, 1.90]
- n=30 → CI [0.98, 2.02] (하한 1.0 근접)

### p-value (random walk null)
- 50 trades, 승률 55% → p≈0.31 (무의미)
- 승률 60% → p≈0.06 (경계)
- **승률 65%+ 만 p<0.05 도달**

### Overfitting (Bailey et al. 2014)
- Walk-forward 없이 in-sample만 = 미래 성과 50% 미만 확률 60-70%
- RSI 표준 파라미터로 보정 → **45-55%**

### Regime bias (가장 큰 리스크)
- 90일 = 1-2 regime 커버
- BTC regime 전환 후 RSI mean reversion 붕괴 확률 **70-80%**

### Selection bias
- BTC = 효율적 시장 (RSI mean reversion 효과 가장 약함)
- 기간 선택 → Sharpe ±0.5 가능
- 총 **Sharpe 0.3-0.8 과대추정**

## 백테스트의 실제 가치 (신뢰 가능)

1. **버그 탐지**: 로직 오류, 슬리피지 미반영, look-ahead bias 제거
2. **Fast-fail gate**: Sharpe < 0.5 또는 INSIGHT-007 fee 함정 → 즉시 폐기

## 신뢰 불가
- 절대 수익률 예측
- 실제 Sharpe 수준 예측
- Regime 전환 후 안정성

## Polaris 적용 ([[ADR-010]] 결정)

**방향 B**: 백테스트 = fast-fail gate (사전 sanity) + 페이퍼 트레이딩 즉시 병행 (진짜 검증).

### 60_alpha 워크플로 수정
```
HYPOTHESIS → BACKTEST (fast-fail + Sharpe>0.5 sanity)
           → PAPER 30일 즉시 시작 (in parallel with backtest 보강)
           → Promotion Gate (paper actual ≥ backtest 50% 합의 시)
           → ADR 승격
```

### Promotion Gate 기준 강화
- Sharpe ≥ 0.5 (codex 권장 — 0 → 0.5 상향)
- 승률 ≥ 52% (random walk null 약간 위)
- n_trades ≥ 30 (통계 최소)
- expectancy > fee_round_trip (fast-fail)
- max_drawdown ≤ 10%

### 리스크 관리 (백테스트 신뢰도 낮음 보수적)
- 단일 포지션 ≤ 2% balance
- 일일 손실 한도 ≤ 5% balance

## Recommendation
- [ ] ADR-010 (Backtest+Paper 병행 결정) 작성
- [ ] promotion_gate.py MIN_SHARPE 0.5로 update + win rate check 추가
- [ ] Phase 2c 신설: 페이퍼 인프라 (WS feed + 주문 simulation)
- [ ] HYPOTHESIS-001 워크플로 update (백테스트 fast-fail + 페이퍼 30일)

## Related
- INSIGHT-002 (MTTR-alpha)
- INSIGHT-007 (OKX SPOT fee 수학)
- ADR-009 (SPOT vs PERP — 페이퍼 결과로 트리거)
- 60_alpha/_README (워크플로)
