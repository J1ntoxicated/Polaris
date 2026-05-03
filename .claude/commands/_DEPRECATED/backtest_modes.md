# Backtest — 3-Mode 상세

[backtest.md](backtest.md) 모드별 실행 상세.

## Mode 1: Parameter Sweep (default)
기존 트레이드를 수정 파라미터로 재생:
- `hard_stop` / `trailing` / `profit_cap` 변경 → exit 재계산
- Sentiment threshold → 이력 기반 entry 재계산
- 보유 시간 분석 — "N시간 더 보유했다면?"
- 캔들 기반 technical indicator (RSI/BB/SMA 멀티 타임프레임)

## Mode 2: Grid Search
다중 파라미터 조합 체계적 테스트:

1. **Grid 생성**: 사용자 범위 또는 현재값 ±20% 자동
2. **시뮬**: tick-by-tick `price_path` 있으면 정밀, 없으면 entry/exit/max/min 근사
3. **결과 매트릭스**:
```
hard_stop | trail_act | WR    | RR   | Net$   | MaxDD  | Sharpe
-1.5%     | 1.5%      | 25.0% | 0.28 | -$5.49 | -20%   | -0.3
-2.0%     | 2.0%      | 38.0% | 0.95 | +$2.10 | -15%   | 0.8
-2.5%     | 2.5%      | 42.0% | 1.12 | +$8.30 | -18%   | 1.1  ← BEST
```
4. **Optimal combo**: WR / RR / Sharpe 기준 + DD 제약
5. **Walk-forward**: 70/30 train/test (overfitting 가드)

## Mode 3: Strategy Lab
신규 전략 가설 A/B 검증:

1. **가설**: 변경안 + 기대 효과
2. **Historical replay**: "이 필터 있었다면 어떤 트레이드 차단?"
3. **A/B 비교**:
```
           | Current | With Filter | Delta
Trades     |    23   |     14      | -39%
WR         |  43.5%  |    57.1%    | +13.6%
Net P&L    | -$137   |    +$28     | +$165
VERDICT: STRONG IMPROVEMENT
```
4. **Risk**: 트레이드 감소 (opportunity cost) / overfit / regime sensitivity

---

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
