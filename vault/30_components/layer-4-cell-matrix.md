---
type: component
component: layer-4-cell-matrix
status: active
date_created: 2026-05-06
tags: [layer-4, cell-matrix, t11, routing, quartile, ewma]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-005-sizing-formula-cell-routing|ADR-005]], [[ADR-006-cell-matrix|ADR-006]], [[layer-3-sizing-risk]]
reviewed_by: codex+jin (round 1, gpt-5.4)
---

# Layer 4 — Cell Matrix

## Decision (codex 합의 R1)

**P0 = 4-dim primary** (`exchange × strategy × ticker × regime`) + **group/session shadow sub-dim** (8-dim 승격 = `closed_trades >= 1000` AND `subcell n_eff >= 20`). **Score = `avg_pnl_r × √n_eff / 70`** (R-multiple, baseline_n=70 fixed). **Dynamic quartile activation gate** (`eligible cells (n_eff>=5) >= 20`). **Warmup shrinkage** (5 ≤ n_eff < 20 = parent3/parent2 blend). **EWMA decay half-life 7d**. **Routing mult = top ×1.5 / bottom ×0.5 / mid ×1.0 / sparse(n<5) ×1.0** (P0 aggressive 상방 ↑, ADR-005 patch 권고).

## Detail Spec

### Q1 — 4-dim P0 primary + shadow context
**P0 routing SSOT** = `exchange × strategy × ticker × regime` (cardinality 15k).

**Shadow stats** (수집만, routing X): `group × session` 추가 → 첫 24h sparse 회피 + 추후 8-dim expand 데이터 축적.

**8-dim 승격 조건**: `portfolio closed_trades >= 1000` AND `candidate subcell n_eff >= 20`. `direction × liquidity_tier` 까지 P0 = 첫 24h neutral 폭증 → P1 이후.

### Q2 — Score formula
```python
score = avg_pnl_r × √n_eff / 70
```
- `avg_pnl_r` = R-multiple (notional 정규화) — venue/ticker cross-comparable.
- `n_eff` = EWMA-decayed sample count (Q5 참조).
- baseline_n = **70 fixed** (P0 global constant). learner-tunable X (demo 라고 confidence baseline 낮추면 "빨리 과신").
- 음수 score (loser cell) 그대로 유지 (floor 0 X).

### Q3 — Dynamic quartile + activation gate
- **Method**: dynamic quartile (활성 cell pool 분포 매번 재계산).
- **Activation gate**: `eligible cells (n_eff >= 5) >= 20` 조건.
  - 그 전: 모든 cell `×1.0` neutral.
  - eligible >= 20 도달 = 상하위 5개씩 형성 → ranking 의미.
- absolute threshold (score > 0.3) 거부 — cold start 무용.

### Q4 — Sparse cell + warmup shrinkage
| n_eff range | Routing | 근거 |
|---|---|---|
| `n_eff < 5` | **×1.0 neutral** (parent fallback X) | ticker-specific SSOT 의미 보존 |
| `5 ≤ n_eff < 20` | **blended score** = `(n/20)·cell + ((20-n)/20)·parent` | warmup noise 감쇠 |
| `n_eff >= 20` | cell score 그대로 | 충분 sample |

**Parent 우선순위** (warmup blend): `parent3 (exchange × strategy × regime)` → `parent2 (strategy × regime)` → `0.0`.

### Q5 — EWMA decay (half-life 7d)
- decay 없음 → stale winner 영원히 (위험).
- sliding window → sparse 환경 too brittle.
- **EWMA exponential decay, half-life = 7 days** (P0 default).

```python
factor = exp(-elapsed_sec * ln(2) / (7 * 86400))
n_eff_new = n_eff * factor + 1
pnl_r_sum_eff_new = pnl_r_sum_eff * factor + new_pnl_r
```

Sparse cell 에서도 window 보다 잘 동작. update cost 낮음 (per-trade incremental).

### Q6 — Routing mult (aggressive 상방 ↑)
| Quartile | P0 codex 제안 | ADR-005 carryover | 결정 |
|---|---|---|---|
| top | ×1.5 | ×1.3 | **×1.5** (aggressive bias) |
| bottom | ×0.5 | ×0.5 | ×0.5 (학습 유량 보존) |
| mid | ×1.0 | ×1.0 | ×1.0 |
| sparse n<5 | ×1.0 | ×1.0 | ×1.0 |

**Patch 권고**: ADR-005 §Cell Routing Mult `×1.3` → `×1.5` 변경 (L3 hard-cap 체인 이미 있음, composition 안전).

ELO winner-only 미래 확장 (ADR-002 C 메커니즘) = +0.05/100 trades, max 3.0×, ELO winner 한정 → 본 quartile mult 와 multiplicative.

## Implementation notes

### File layout (P0)
```
polaris/core/cell_matrix/
├── schema.py    # CellKeyP0 / CellContext dataclass
├── score.py     # avg_pnl_r + cell_score + EWMA decay + warmup shrinkage
└── routing.py   # quartile classify + compute_routing_mult + update on close
```

### Schema (SQLite)
```sql
CREATE TABLE cell_matrix_p0 (
  exchange TEXT NOT NULL,
  strategy TEXT NOT NULL,
  ticker TEXT NOT NULL,
  regime TEXT NOT NULL,
  n_eff REAL NOT NULL DEFAULT 0.0,
  wins_eff REAL NOT NULL DEFAULT 0.0,
  pnl_r_sum_eff REAL NOT NULL DEFAULT 0.0,
  avg_pnl_r REAL NOT NULL DEFAULT 0.0,
  score REAL NOT NULL DEFAULT 0.0,
  last_closed_ts INTEGER NOT NULL,
  PRIMARY KEY (exchange, strategy, ticker, regime)
);

CREATE TABLE cell_matrix_parent3 (
  exchange TEXT NOT NULL, strategy TEXT NOT NULL, regime TEXT NOT NULL,
  n_eff REAL DEFAULT 0.0, pnl_r_sum_eff REAL DEFAULT 0.0,
  avg_pnl_r REAL DEFAULT 0.0, score REAL DEFAULT 0.0,
  last_closed_ts INTEGER NOT NULL,
  PRIMARY KEY (exchange, strategy, regime)
);

CREATE TABLE cell_matrix_parent2 (
  strategy TEXT NOT NULL, regime TEXT NOT NULL,
  n_eff REAL DEFAULT 0.0, pnl_r_sum_eff REAL DEFAULT 0.0,
  avg_pnl_r REAL DEFAULT 0.0, score REAL DEFAULT 0.0,
  last_closed_ts INTEGER NOT NULL,
  PRIMARY KEY (strategy, regime)
);

-- Shadow context (수집만, routing X)
CREATE TABLE cell_matrix_shadow_context (
  exchange TEXT, strategy TEXT, ticker TEXT, regime TEXT,
  grp TEXT, session TEXT,
  n_eff REAL DEFAULT 0.0, pnl_r_sum_eff REAL DEFAULT 0.0,
  avg_pnl_r REAL DEFAULT 0.0, score REAL DEFAULT 0.0,
  last_closed_ts INTEGER NOT NULL,
  PRIMARY KEY (exchange, strategy, ticker, regime, grp, session)
);
```

### Dataclass + signatures
```python
@dataclass(frozen=True)
class CellKeyP0:
    exchange: str; strategy: str; ticker: str; regime: str

@dataclass(frozen=True)
class CellContext:
    group: str; session: str
    direction: str; liquidity_tier: str

# score.py
def apply_exponential_decay(value, *, elapsed_sec, half_life_sec) -> float
def compute_avg_pnl_r(*, pnl_r_sum_eff, n_eff) -> float
def compute_cell_score(*, avg_pnl_r, n_eff, baseline_n=70.0) -> float
def resolve_effective_score(key, *, cell_score, cell_n_eff,
    parent3_score: float | None, parent2_score: float | None,
    shrinkage_n=20.0) -> float

# routing.py
def update_on_trade_close(key, ctx, *, pnl_r, won, closed_ts) -> None
def classify_quartile(scores: Sequence[float], score: float) -> str
def compute_routing_mult(key, *, eligible_scores, n_eff,
    effective_score, min_live_n=5.0, min_pool_size=20,
    top_mult=1.5, bottom_mult=0.5) -> float
```

### Warmup rules (요약)
- `n_eff < 5`: `mult = 1.0` (neutral)
- `5 ≤ n_eff < 20`: `effective_score = (n/20)·cell + ((20-n)/20)·parent`
- parent: `parent3 → parent2 → 0.0`
- eligible pool: `n_eff >= 5` cells 만
- pool `< 20`: 전부 `×1.0`

### Constants (P0)
```python
CELL_BASELINE_N = 70.0
CELL_DECAY_HALF_LIFE_SEC = 7 * 86400
CELL_MIN_LIVE_N = 5.0
CELL_MIN_POOL_SIZE = 20
CELL_SHRINKAGE_N = 20.0
ROUTING_TOP_MULT = 1.5         # ADR-005 patch (1.3 → 1.5)
ROUTING_BOTTOM_MULT = 0.5
ROUTING_MID_MULT = 1.0
EIGHT_DIM_PROMOTION_THRESHOLD = 1000  # portfolio closed trades
```

### P0 vs P1 split
- **P0**: 4-dim primary + parent3/parent2 fallback + EWMA 7d + dynamic quartile gate (>=20) + shadow group/session 수집.
- **P1**: shadow → ranking 보조 + AI feedback learner cell weight delta + 8-dim 승격 (>=1000 trades).
- **P2**: ELO winner-only +0.05/100 trades, max 3.0× (multiplicative on top of quartile mult).

## Risk + Aggressive Mitigation

### Risk
1. 첫 48h quartile churn (eligible pool <20 → activation 지연).
2. 빠른 regime flip 시 stale winner persistence.

### Aggressive Mitigation
- `top ×1.5` 로 상방 개방 (ADR-005 patch).
- `n<5 neutral` + `eligible>=20 gate` + `7d EWMA decay` + `warmup shrinkage` 동시 적용 = 과잉 확신만 차단, sparse noise 잘라냄.
- `bottom ×0.5` 유지 (×0.3 거부 — loser cell 너무 빨리 죽이면 recovery sample X).
- Shadow context 수집으로 8-dim expand 시 즉시 활성 가능.

## ADR Patch 권고
- **ADR-005** §Cell Routing Mult: `top ×1.3` → **`top ×1.5`** 변경 권고. L3 hard-cap 체인이 이미 composition 보호.
- **ADR-006** §Routing Decision: warmup shrinkage 5-19 구간 + EWMA decay half-life 7d + dynamic quartile activation gate (>=20) 추가 권고.

## Sources
- codex round 1 (`/tmp/polaris_phase0/L4_r1_response.md`, gpt-5.4)
- ADR-006 §8-dim, P0 4-dim 압축, score formula
- ADR-005 §Cell Routing Mult (patch 권고)
- T11 archive `cell_matrix.py` (avg_pnl × √n / 70 calibration)
- [[layer-3-sizing-risk]] (cell_routing_mult sizing factor 통합 위치)

## Implementation status (2026-05-07 — P0 Day 2)

- **Status**: implemented + codex round 3 APPROVE.
- **Code**: `polaris/core/cell_matrix/{schema,score,routing,__init__}.py` (~700 LOC)
  - `update_on_trade_close` — atomic SQLite transaction over p0 + parent3 + parent2 + shadow.
  - `resolve_routing_for_cell` — SSOT: read decayed cell + parents → blend (warmup) → quartile classify on the live decayed pool → mult.
  - `load_eligible_scores_decayed` — read-time EWMA decay, drops cells whose decayed `n_eff < 5`.
  - `compute_cell_score`, `decay_factor`, `resolve_effective_score` — pure helpers covered by hypothesis property tests.
- **Production caller**: only `polaris/core/sizing/cell_mult_application.py::apply_cell_routing_mult` (Day 2 Layer 3 skeleton). Guarantees the warmup/decay path cannot be silently bypassed.
- **Tests**: 32 unit + property tests (`tests/test_layer4_cell_matrix.py`) + 7 sizing tests asserting top×1.5 / bottom×0.5 / cold×1.0 / NaN / negative-reject path.
- **Codex review history**: REJECT (round 1 — dead-code SSOT) → REJECT (round 2 — no prod caller) → APPROVE (round 3 — sizing seam wired).
- **Schema rows**: `cell_matrix_p0` / `cell_matrix_parent3` / `cell_matrix_parent2` / `cell_matrix_shadow_context` (provisioned by `polaris/storage/schema.py::ALL_DDL`).
