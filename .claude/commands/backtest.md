# Backtest — Strategy Backtest + Grid Search + Strategy Lab

적용 전 전략 변경을 과거 데이터로 시뮬. Absorbs: `grid-search`, `strategy-lab`.
3-Mode 상세: [backtest_modes.md](backtest_modes.md).

## Usage
```
/backtest hard_stop -1.0 to -2.0 step 0.1                              — sweep
/backtest grid hard_stop [-1.5,-2.0,-2.5] trail_activate [1.5,2.0,2.5] — grid
/backtest lab "Add OKX funding < -0.01% filter for crypto longs"       — hypothesis
```

## 3-Mode 개요
| Mode | 용도 | 출력 |
|------|------|------|
| 1. Sweep | 단일 파라미터 | WR/RR/Net 곡선 |
| 2. Grid | 다중 조합 탐색 | 매트릭스 + BEST |
| 3. Lab | 신규 가설 A/B | Delta 비교 + Verdict |

상세: [backtest_modes.md](backtest_modes.md)

## Overfitting Safeguards
- <50 trades → "low confidence" 경고
- <100 trades → grid search 비권장
- 모든 grid 결과 walk-forward 검증
- Bonferroni correction (multiple comparisons)
- Slippage·spread 미반영 — 실제는 더 나쁠 수 있음

## Output → Apply
- Optimal params → `/param-tune` 즉시 적용
- 중요 변경 → `/debate` 3-AI 검증
- 코드 로직 변경 → 구현 계획

## Data Sources
- `data/candles/` (Yahoo + OKX)
- `data/trade_stats.jsonl` (price_path 필드)
- `data/okx_paper_trades.jsonl`, `data/sentiment_history.jsonl`, `data/live_config.json`

---

> 🔴 **Vault mandatory** — SSOT: [[vault_mandatory_protocol]]. 진입 read `_NOW.md` + entity, 종료 write 1+ (INSIGHT/ADR/digest/audit). Entity 링크 의무.
