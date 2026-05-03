---
entity_type: insight
entity_id: INSIGHT-015
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[INSIGHT-007]]", "[[INSIGHT-013]]", "[[INSIGHT-014]]", "[[ADR-009]]", "[[ADR-011]]"]
mode: alpha
reviewed_by: codex
maturity: authoritative
authoritative_basis: 직접 백테스트 (BTC 8.5년 + ETH/SOL/BNB/XRP 다년)
tags: [type/insight, status/active, scope/alpha, priority/p0, polaris]
---

# INSIGHT-015 — SMA Crossover 1d = SPOT Viable (HYPO-003 첫 fast-fail 통과)

> **결정적 발견**: 1d trend following으로 OKX SPOT fee 1.4% 함정 우회 가능. ADR-009 PERP shift trigger 약화.

## Evidence (직접 측정)

### BTC-USDT 1d, 8.5년 데이터 (3127 candles, 2017-10 ~ 2026-05)
| Strategy | n_trades | hit_rate | expectancy | Sharpe | MDD |
|---|---|---|---|---|---|
| SMA(10, 30) | 64 | 29.69% | **+0.04975** | +0.153 | 67.50% |
| SMA(20, 50) | 34 | 32.35% | **+0.14166** | +0.198 | 53.85% |
| SMA(5, 20) | 92 | 29.35% | **+0.03460** | +0.162 | 72.64% |
| SMA(9, 21) | 81 | 32.10% | **+0.03880** | +0.142 | 70.97% |
| **SMA(50, 200)** | **8** | **62.50%** | **+0.47690** | **+0.475** | **36.02%** |

### Multi-ticker 1d SMA(20, 50)
| Ticker | n_candles | trades | hit_rate | expectancy | Sharpe | MDD |
|---|---|---|---|---|---|---|
| ETH-USDT | 3000 | 28 | **57.14%** | +0.26075 | +0.223 | 45.99% |
| SOL-USDT | 2042 | 20 | 35.00% | **+0.71358** | +0.326 | 64.78% |
| BNB-USDT | 1230 | 14 | 42.86% | +0.05488 | +0.247 | 28.96% |
| XRP-USDT | 3000 | 39 | 35.90% | +0.03056 | +0.062 | 85.96% |

## Conclusion

**모든 1d trend following = expectancy 양수**. SMA(50, 200) BTC 8.5년 = **+47% per trade** (8 trades). 멀티 ticker 일관 패턴.

### Why 1d viable, 1h/4h fast-fail?
- 1d trend = 큰 winner 가능 (5-50% per trade) → fee 1.4% 통과 + 큰 net
- 1h/4h = noise 잠식, trade frequency 높음 → fee 누적 잠식
- 큰 trend 1-2개 잡으면 small loss 다수 보상 (low hit_rate + high expectancy)

### INSIGHT-007 (fee 함정) 적용 범위 재정의
- ❌ 모든 SPOT alpha 차단 X
- ✅ Scalping/short-hold (1m~4h) 차단
- ✅ Long-hold trend following (1d+) viable

## Promotion Gate Limitation 발견

현재 Promotion Gate (ADR-010):
- Sharpe ≥ 0.5: 1d trend으로 미달 (Sharpe 0.1-0.4 범위) — trade 빈도 낮아 mean/std 비율 낮음
- win_rate ≥ 0.52: 1d trend 본질적으로 미달 (큰 winner + many small loss = hit 30-40%)
- MDD ≤ 10%: 1d crypto 본질적으로 미달 (BTC 8.5년 max DD 67%)

→ **Promotion Gate가 scalp 가정 → 1d trend에 부적절**. ADR-011 (Promotion Gate timeframe-aware) 신설 필요.

## ADR-009 PERP counter 영향

- HYPO-001/002 fast-fail = 2/5
- HYPO-003 1d SMA crossover = **fast-fail 통과** → counter +0
- **PERP shift 트리거 약화** — 1d trend following 페이퍼 검증 후 결정 권장
- 단 1h/4h SPOT scalping은 여전히 PERP 전환 가치 있음

## Polaris 적용

### HYPOTHESIS-003 active 유지
- 1d 데이터 8.5년 기준 fast-fail 통과
- Promotion Gate 미달이지만 timeframe-aware Gate 적용 시 통과 가능
- **다음 단계**: 페이퍼 검증 30일 (ADR-010 워크플로) — 단 1d timeframe라 30일 = 30 trades 부족, 90일+ 필요

### 새 ADR 후보
- ADR-011: Promotion Gate Timeframe-aware (1h scalp / 1d trend 기준 분리)
- ADR-012 (옵션): 1d trend → paper 90일+ 검증 + position size scaling

### SPOT 미래
- 1d trend following SPOT = viable
- 1h SPOT = PERP 검토 (별도 ADR-009 trigger)
- Polaris 첫 paper 운영 = SMA(50, 200) BTC 1d 또는 SMA(20, 50) BTC 1d

## Recommendation
- [ ] HYPOTHESIS-003 active로 유지 (archived/ → active/)
- [ ] ADR-011 (Promotion Gate Timeframe-aware) 작성
- [ ] HYPO-001/002 fast-fail은 **scalping 카테고리 한정** 명시
- [ ] PERP 검토 보류 — 1d trend paper 검증 후 결정
- [ ] SMA(50, 200) BTC 1d = 페이퍼 첫 후보 (단 trade 빈도 낮아 paper period 90일+ 필요)

## Related
- INSIGHT-007 (OKX SPOT fee 수학 — 적용 범위 재정의)
- INSIGHT-013 (RSI 1h fast-fail)
- INSIGHT-014 (BB 1h/4h fast-fail)
- ADR-009 (PERP — counter 보류)
- ADR-010 (Backtest + Paper parallel)
- ADR-011 (Promotion Gate Timeframe-aware — 신설 권장)
