---
type: ADR
adr_id: ADR-006
aliases: [ADR-006]
status: active
date_created: 2026-05-06
tags: [adr, cell-matrix, t11, routing]
related: [[ADR-003]], [[ADR-005]], [[ADR-007]], [[active-autonomous-vision]]
reviewed_by: codex+jin (T11 archive carryover + Jin clarification 21:30)
---

# ADR-006 — Cell Matrix (8-dim, P0 4-dim 압축)

## Decision

Strategy 결정의 SSOT (Single Source of Truth) = `cell_matrix` table. 8-dim per-cell 통계 누적 → routing 결정 (AMPLIFY / SUPPRESS / PASS).

## 8 Dimensions

```
exchange × group × session × regime × strategy × direction × ticker × liquidity_tier
```

| Dim | Cardinality | Example |
|---|---|---|
| exchange | 2 | okx / capital |
| group | ~7 | spot_intraday_event / cfd_fx_trend / ... |
| session | 3 | asia / eu / us |
| regime | 4 | bull_trend / bear_trend / chop / crisis |
| strategy | 7 | volume_burst / tsmom / ... |
| direction | 2 | long / short |
| ticker | ~270 | BTC-USDT / EURUSD / ... |
| liquidity_tier | 3 | high / mid / low (24h vol bucket) |

**Theoretical cells**: 2 × 7 × 3 × 4 × 7 × 2 × 270 × 3 ≈ 1.9M (sparse).

## P0 압축 (4-dim)

```
exchange × strategy × ticker × regime
```
- Cardinality: 2 × 7 × 270 × 4 ≈ 15k (manageable)
- Group/session/direction/liquidity_tier = ignore P0 (default 평균)
- Sparse: 첫 24h 활성 cell ~50-100 예상

## P1 확장 (8-dim full)
- 누적 trade ≥1000 이후 expand
- Sparse cell handling: n<5 → ×1.0 default + parent cell (4-dim) 평균 fallback

## Cell Stat Schema

```sql
CREATE TABLE cell_matrix (
  exchange TEXT,
  strategy TEXT,
  ticker TEXT,
  regime TEXT,
  -- P1 추가:
  -- group TEXT, session TEXT, direction TEXT, liquidity_tier TEXT,
  n_trades INTEGER DEFAULT 0,
  win_rate REAL DEFAULT 0.0,
  avg_pnl REAL DEFAULT 0.0,
  score REAL DEFAULT 0.0,
  last_updated INTEGER,
  PRIMARY KEY (exchange, strategy, ticker, regime)
);
```

## Score Formula (T11 archive)

```
score = avg_pnl × √n_trades / 70
```

- `avg_pnl` = trade-level R-multiple (notional 정규화)
- `√n` = sample size confidence weight
- `/70` = normalization constant (T11 calibration, 70 trades = high confidence threshold)

## Routing Decision (Phase 0 L4 patch)

```python
def cell_routing_mult(cell_stat, eligible_pool_size):
    # Warmup shrinkage: small-sample cells blended toward parent
    if cell_stat.n_eff < 5:
        return 1.0  # cold, parent3 default (passthrough)
    if 5 <= cell_stat.n_eff < 20:
        # blend toward parent3 (3-dim aggregate) and parent2 (2-dim)
        blended = 0.5 * cell_stat.ewma_score + 0.5 * cell_stat.parent3_score
        score = blended
    else:
        score = cell_stat.ewma_score
    
    # Dynamic quartile activation gate: pool eligible cells ≥ 20 만
    if eligible_pool_size < 20:
        return 1.0  # gate not yet active
    
    quartile = compute_quartile(score)
    if quartile == "top":
        return 1.5  # AMPLIFY (Phase 0 L4 — top ×1.3 → ×1.5 상방 개방)
    elif quartile == "bottom":
        return 0.5  # SUPPRESS
    else:
        return 1.0  # PASS
```

- **EWMA half-life 7d**: `cell_stat.ewma_score` = exponentially-weighted score (오래된 trade decay)
- **Warmup shrinkage**: 5 ≤ n_eff < 20 시 parent3/parent2 cell 평균 blend (sparse cell handle)
- **Dynamic quartile activation**: eligible cells (n_eff ≥ 20) ≥ 20 일 때만 quartile routing 활성

## Cell Stat Schema (Phase 0 L4 patch)

추가 컬럼:
```sql
ALTER TABLE cell_matrix ADD COLUMN n_eff REAL DEFAULT 0.0;       -- effective sample (EWMA-decayed)
ALTER TABLE cell_matrix ADD COLUMN ewma_score REAL DEFAULT 0.0;  -- exponentially-weighted score
ALTER TABLE cell_matrix ADD COLUMN parent3_score REAL DEFAULT 0.0; -- 3-dim parent (exchange × strategy × regime)
ALTER TABLE cell_matrix ADD COLUMN parent2_score REAL DEFAULT 0.0; -- 2-dim parent (exchange × strategy)
```

## Update Trigger

- Every closed trade → 해당 cell 의 (n_trades, win_rate, avg_pnl, score) 업데이트
- Atomic: SQLite transaction
- Rollback: 24h trade history replay 가능 (event log)

## Routing 사용 (Layer 3 sizing)

`final_size = T4_size × cell_routing_mult` (hard MAX 절단 전)

## Anti-pattern

- ELO 단일 점수 → cell 8-dim 으로 분해 (ticker × strategy 같은 ELO 간섭 제거)
- aggregate strategy WR → cell 별 WR (specific 차별화)
- Loser 자동 감점 → 본 ADR 은 update 만, suppression 은 routing 결정 (loser 도 ×0.5 까지만, 0 X)

## Phase
- P0: 4-dim 압축, 첫 24h 활성 cell 채움
- P1: 8-dim full, 누적 ≥1000 trades 후 expand
- P2: cell-level ELO winner-only 증액 (max 3.0×, [[ADR-002]] C 메커니즘)

## Sources
- T11 archive: `~/.claude/archive/polaris_memory_pre_v2_2026-05-06/handoff_unified_2026_04_21_T11_*.md`
- Round 3 D1 (isolation 와 양립)
- Jin clarification 21:30 (cell matrix 100% 메트릭스화)
