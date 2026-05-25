---
type: component
component: layer-5-learner-network
status: active
date_created: 2026-05-06
tags: [layer-5, learner, t11, auto-tune, adaptive-learner-attack]
related: [[ADR-003]], [[ADR-006]], [[ADR-007]], [[layer-2-per-gate-pipeline]], [[layer-4-cell-matrix]]
reviewed_by: codex+jin (round 1, gpt-5.4)
---

# Layer 5 — Learner Network (7 learners, hourly auto-tune)

## Decision (codex 합의 R1)

**Incremental stats per trade close + hourly commit** (delta write 즉시 X, sparse churn 봉쇄). **Independent axis composition** (`session × regime × cell_routing × ai_feedback`, 우선순위 X). **Individual mult clip [0.3, 3.0]** + **product clip [0.1, 5.0]** + **±0.1/hour cap**. **SQLite hot backup + JSON manifest snapshot**, **auto-flag rollback (manual apply)**. **adaptive_learner_attack 영속 4 원칙 = SQLite block registry triple (ticker, strategy, regime) 1h auto-unblock**. **AI feedback ≠ cell matrix** (cell = realized SSOT, ai = behavioral overlay). **Sparse fallback automatic** (`n_eff < 20 → neutral`, max_hold = `expected_holding_bars` 까지 fallback).

## Detail Spec

### Q1 — Hourly trigger (incremental stats + hourly commit)
- **`session_mult`**: key = `strategy_id:session`, hourly recalibration only (session edge 느리게 변).
- **`regime_mult`**: key = `strategy_id:regime`, trade close 마다 stats update + regime flip 시 current key refresh, **delta commit = hourly**.
- **`max_hold`**: key = `strategy_id`, trade close 마다 holding outcome bucket update, 다음 hour 부터 적용.
- WR threshold 55%/40% 유지 (ADR-007). 최소 표본 = `n_eff >= 20` (delta write 조건).
- **이유**: close 즉시 write = sparse churn 폭증. P0 = `incremental stats + hourly commit`.

### Q2 — Conflict resolution (independent axis composition)
- 우선순위 없음. 항상 `final = session × regime × cell_routing × ai_feedback` 순서 합성.
- 같은 learner 내부 같은 hour 상반 delta = `net_delta` 합산 후 `±0.1/hour` cap.
- **Individual mult clip**: `[0.3, 3.0]` (ADR-007 carryover, [0.2, 5.0] 거부 — saturation 빠름).
- **Final product clip**: `[0.1, 5.0]`.
- **Production caller (2026-05-26)**: `polaris.core.sizing.engine.compute_size` 가 매 sizing 시 `SessionMultLearner.get_mult` + `RegimeMultLearner.get_mult` + `evaluate_triple_block` 호출 → `clip_product_mult(s × r × b)` 결과를 T4 chain 의 단일 mult 로 곱함. cell_routing 은 별개 (T4 별도 항). ai_feedback = P1 (스텁).

### Q3 — Rollback (SQLite hot backup + auto-flag manual apply)
- Snapshot: `data/learner_snapshots/<ts>.db` + `data/learner_snapshots/<ts>.json` (manifest).
- SQLite hot backup → atomic restore + replay 단순.
- **Auto-rollback X (auto-flag만)**:
  - 기준: `closed_trades_since_snapshot >= 20` AND `rolling_expectancy_24h_post <= 0.5 × rolling_expectancy_24h_pre`
  - Detector 가 `rollback_candidates` row 생성 → `tuning-learners` skill 또는 운영 job 이 manual 적용.
- **Granularity**: snapshot change-set 단위 default + 단일 learner 만 바뀐 hour 면 learner-local rollback 허용.

### Q4 — adaptive_learner_attack (SQLite block registry)
**`learner_blocks` table** = (ticker × strategy × regime) triple, 1h auto-unblock.
- **block 조건**: `n_eff >= 20` AND `win_rate <= 0.30` AND `expectancy_r <= -0.25`
- **block action**: hard block X. `size_mult = 0.3` (mult 적용, 진입 자체는 허용).
- `blocked_until_ts = now + 3600` (1h 후 auto-unblock, eviction X).
- 영속 = SQLite (in-memory X, 프로세스 재시작 후 unblock 보존).
- **learner failure toggle**: `enabled=false` → 즉시 hardcoded defaults 복귀.
- 4 원칙 매핑:
  - 관대 default = "증거 부족이면 허용" (`n_eff < 20` 면 block X).
  - 일시 차단 = `blocked_until_ts` SQLite ts.
  - Specific = (ticker, strategy, regime) triple.
  - Toggle = `enabled` flag + default registry.

### Q5 — AI feedback (#7) — behavioral overlay
**cell matrix ≠ ai feedback**:
- `cell_matrix` = realized outcome SSOT (R-multiple, EWMA).
- `ai_feedback` = behavioral overlay (lesson-driven).

**Live weight 조정 axis**:
- `strategy × regime`: `ai_feedback_weight` scalar delta
- `strategy × session`: `ai_feedback_weight` scalar delta
- `validator_threshold_bias`: Gate 3 threshold offset delta

**Soft mode** (closed_trades < 100): stored delta 의 **25% 만 live** 반영.
**Live mode** (≥ 100 closed trades): 100% 반영.

**Lesson taxonomy** (P0, 5개):
- `entry_timing` / `exit_patience` / `overtrade` / `stop_discipline` / `conviction_mismatch`

cell matrix score 직접 건드리지 않음 (분리 원칙).

### Q6 — Sparse data fallback (automatic)
| Learner | Fallback (n_eff < 20) | Live mode 조건 |
|---|---|---|
| `session_mult` | `1.0` neutral | `n_eff >= 20` per key |
| `regime_mult` | `1.0` neutral | `n_eff >= 20` per key |
| `max_hold` | `StrategyMetadata.expected_holding_bars` (ADR-008) | `strategy_id n_closed >= 20`: ±20% / `>=50`: ±40% |

`session_mult` portfolio closed `<50` 동안 = observe-only 권장.
Sparse fallback toggle 시스템 자동, skill = override 용 only.

## Implementation notes

### File layout (P0)
```
polaris/core/learners/
├── base.py             # BaseLearner abstract + clip helpers
├── session.py          # session_mult learner
├── regime.py           # regime_mult learner
├── max_hold.py         # max_hold learner
└── ai_feedback.py      # P0 stub (P1 full)
polaris/storage/
└── learner_store.py    # learner_state / learner_blocks / rollback_candidates CRUD
polaris/jobs/
└── learner_hourly.py   # hourly commit job + snapshot creator
```

### Schema (SQLite)
```sql
CREATE TABLE learner_state (
  learner_id TEXT NOT NULL,
  key TEXT NOT NULL,             -- "vol_burst:asia" / "tsmom:bull_trend"
  value REAL NOT NULL,
  n_eff REAL NOT NULL DEFAULT 0.0,
  wins_eff REAL NOT NULL DEFAULT 0.0,
  pnl_r_sum_eff REAL NOT NULL DEFAULT 0.0,
  pending_delta REAL NOT NULL DEFAULT 0.0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (learner_id, key)
);

CREATE TABLE learner_blocks (
  ticker TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  regime TEXT NOT NULL,
  size_mult REAL NOT NULL DEFAULT 0.3,
  reason TEXT NOT NULL,
  source_learner TEXT NOT NULL,
  blocked_until_ts INTEGER NOT NULL,
  created_ts INTEGER NOT NULL,
  PRIMARY KEY (ticker, strategy_id, regime)
);

CREATE TABLE rollback_candidates (
  snapshot_ts INTEGER PRIMARY KEY,
  learner_scope TEXT NOT NULL,
  expectancy_pre REAL NOT NULL,
  expectancy_post REAL NOT NULL,
  trade_count INTEGER NOT NULL,
  status TEXT NOT NULL    -- detected/applied/rejected
);
```

### Function signatures
```python
def record_trade_close(trade: ClosedTrade) -> None
def compute_hourly_deltas(now_ts: int, learner_id: str) -> dict[str, float]
def apply_learner_delta(learner_id: str, key: str, delta: float, now_ts: int) -> float
def resolve_final_size_mult(*, session_mult, regime_mult,
    cell_routing_mult, ai_feedback_weight) -> float
def evaluate_triple_block(*, ticker, strategy_id, regime, now_ts) -> dict | None
def create_snapshot(now_ts: int) -> str    # writes .db + .json manifest
def detect_rollback_candidate(now_ts: int) -> dict | None
def restore_snapshot(snapshot_ts: int, learner_scope: str | None = None) -> None
```

### Constants (P0)
```python
LEARNER_MIN_NEFF_FOR_DELTA = 20.0
LEARNER_DELTA_HOURLY_CAP = 0.1
LEARNER_INDIVIDUAL_MULT_CLIP = (0.3, 3.0)
LEARNER_PRODUCT_CLIP = (0.1, 5.0)
WR_PROMOTE_THRESHOLD = 0.55
WR_DEMOTE_THRESHOLD = 0.40
TRIPLE_BLOCK_NEFF_THRESHOLD = 20.0
TRIPLE_BLOCK_WR_THRESHOLD = 0.30
TRIPLE_BLOCK_EXPECTANCY_THRESHOLD = -0.25
TRIPLE_BLOCK_DURATION_SEC = 3600
ROLLBACK_TRADE_THRESHOLD = 20
ROLLBACK_EXPECTANCY_RATIO = 0.5
AI_FEEDBACK_SOFT_MODE_THRESHOLD = 100
AI_FEEDBACK_SOFT_LIVE_FRACTION = 0.25
```

### P0 vs P1 split
- **P0**: 3 learner (`session_mult / regime_mult / max_hold`) + triple block + hourly snapshot + AI feedback **stub**.
- **P1**: 7 learner full (profit_target / trail_mult / bep_activate 추가) + AI feedback live (Sonnet reflector → behavioral overlay) + meta-learner.
- **P2**: cap 자체 learner-tunable (P0 = config-first 유지).

## Risk + Aggressive Mitigation

### Risk
첫 48h = learner 별 `n_eff` 얕음 → hourly churn + false edge 가장 큼.

### Aggressive Mitigation
- Top-side amplification 유지 (learner 가 `+0.1/hour` 주는 것 보존).
- Sparse 보호: `n_eff >= 20` gate / `±0.1/hour cap` / `1h temp block` (영구 X) / `default fallback`.
- 차단 X: triple block 도 `size_mult=0.3` (진입 자체 가능, aggressive trial 유지).
- AI feedback soft mode 25% = hallucination 자취 누적 방지하면서 lesson 신호 보존.
- max_hold = `expected_holding_bars` (ADR-008) fallback 후 점진적 ±20/40% 확장.

## Sources
- codex round 1 (`/tmp/polaris_phase0/L5_r1_response.md`, gpt-5.4)
- ADR-007 §7 learner + adaptive_learner_attack 4 원칙
- ADR-006 §cell matrix score (분리 원칙)
- [[layer-2-per-gate-pipeline]] §AI feedback Q10 (axis + soft mode + taxonomy)
- [[layer-4-cell-matrix]] §EWMA decay + warmup shrinkage (sparse 보호 일관성)
- T11 archive `feedback_adaptive_learner_attack` 영속 원칙
