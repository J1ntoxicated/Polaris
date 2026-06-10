---
type: component
component: layer-6-live-recalc
status: active
date_created: 2026-05-06
tags: [layer-6, live-recalc, regime-flip, strategy-swap, conviction-stacking, adaptive-exit]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-006-cell-matrix|ADR-006]], [[layer-2-per-gate-pipeline]], [[layer-3-sizing-risk]], [[layer-4-cell-matrix]], [[layer-1-canonical-baseline]]
reviewed_by: codex+jin (round 1, gpt-5.4)
---

# Layer 6 — Live Recalc + Self-Correction

## Decision (codex 합의 R1)

**Per-tick full recalc 폐기 → tick-triggered dirty mark + 5s per-position cadence** (L6 hard cap = 50 active positions, batch p95 <250ms). **Regime SSOT = `venue × underlying_group_id`** (cross-venue 다른 regime 가능), 5m bar close 판정, **crisis 즉시 flip / 나머지 2 consecutive close 확인**. **Strategy swap = `entry_strategy_id` immutable + `active_strategy_id` mutable + append-only segment ledger**, P0 max 1 swap/trade. **Conviction stacking = same venue/symbol/side/**strategy** only, 4-gate, max 3 layers (1.0/0.7/0.5), wait (rotate 금지)**. **Adaptive exit dirty-trigger 5종, override count reset = close only**.

## Detail Spec

### Q1 — Tick-triggered dirty + 5s cadence (per-tick full 폐기)
- L2 합의 5초 cadence 유지. tick 마다 full recompute X (state churn > calc cost).
- **P0 budget**: 50 positions × 5s = 10 checks/sec, batch p95 < 250ms.
- Universal 3-layer formula `final = base × ticker_technical(live) × regime_mult` Python deterministic, p95 < 5ms per position.
- 공격성 보존: `dirty trigger 발생 시 즉시 앞당김` (5초 floor cadence + dirty-mark 통한 burst 가능).

### Q2 — Regime flip (venue × underlying_group_id SSOT)
**3-axis classifier** (5m bar close):
- **trend**: `4h EMA20/EMA50 cross + 4h EMA20 slope`
- **volatility shock**: `24h ATR / baseline_p50_atr`
- **chop filter**: `5m-1h efficiency ratio` 또는 ADX-like trend quality

**Rule**:
- `crisis`: `atr_ratio >= 2.2` OR `1h abs move >= 2.5 × baseline_p50_atr`
- `bull_trend`: EMA20 > EMA50, slope > 0, chop filter 통과
- `bear_trend`: EMA20 < EMA50, slope < 0, chop filter 통과
- else `chop`

**Flip confirmation**:
- `crisis` = 1회 즉시 flip
- 나머지 = 2 consecutive 5m closes 확인

OKX BTC vs Capital BTC = 다른 regime 가능 (cross-venue 별 SSOT).

### Q3 — Strategy swap (entry immutable + active mutable, max 1/trade)
- Position 객체: `entry_strategy_id` (immutable) + `active_strategy_id` (mutable).
- ledger = append-only segments table.
- **P0 cap**: max **1 swap / trade**.
- **Constraints** (L2 합의): same correlation_group + same side + same venue/symbol.

**Attribution**:
- 기본 40% (entry) / 60% (active).
- 후행 strategy hold time 비중 ≥ 70% → 30/70 조정.
- cell_matrix + learner = `weighted R` 양쪽 strategy cell 반영.
- 각 segment `regime_at_start` 저장 → segment 별 자기 regime cell 적립.

### Q4 — Conviction stacking (4-gate, max 3 layers, wait)
**Stacking 조건** (4 gate 모두 통과):
1. `cell quartile == top` (L4 top quartile)
2. 기존 레이어 합산 ≤ `single_trade_cap × 2.2`
3. 첫 레이어 `unrealized_pnl_r >= +0.5R`
4. L3 `headroom min()` 통과

**Layers**: max **3** (`1.0 / 0.7 / 0.5` size_mult).

**Constraint**: same venue + same symbol + same side + **same strategy**.
**cap 충돌**: `wait` (기존 rotate 금지 — winner 자르면 aggressive 죽음).

### Q5 — Adaptive exit override (dirty-triggered 5초)
**Dirty triggers** (5종, OR):
- `abs(mid - last_seen_mid) >= 0.25 × ATR`
- `regime flip`
- `unrealized_pnl_r` 가 `+0.7R / +1.5R / +2.5R` 경계 통과
- `strategy swap`
- `base stop` 자체 변경

**Enforce** (per-position state):
- `30s cooldown`: `position_live_recalc_state.cooldown_until_ts` 체크
- `override_count`: widen 채택 시에만 증가
- `override_count_reset_on = close only` (regime flip/24h reset 거부 — quota 우회 차단)
- `5회 도달` → `locked_widen = 1`, but 기본 protective exit 는 계속 작동

## Implementation notes

### File layout (P0)
```
polaris/core/live_recalc/
├── tick_recalc.py       # dirty mark + 5s cadence + Universal 3-layer
├── regime_flip.py       # 3-axis classifier + 2-close confirm
├── strategy_swap.py     # entry/active immutable split + segment ledger
├── conviction.py        # 4-gate stacking + layer mult
├── adaptive_exit.py     # dirty-trigger override + cooldown + count cap
└── schema.py            # PositionLiveRecalcState dataclass
polaris/core/regime/
└── schema.py            # RegimeState dataclass
```

### Schema (SQLite)
```sql
CREATE TABLE position_live_recalc_state (
  position_id TEXT PRIMARY KEY,
  last_check_ts INTEGER NOT NULL,
  last_override_ts INTEGER,
  override_count INTEGER DEFAULT 0,
  dirty_reason TEXT,
  dirty_ts INTEGER,
  cooldown_until_ts INTEGER DEFAULT 0,
  last_seen_mid REAL,
  last_seen_unrealized_pnl_r REAL,
  last_eval_regime TEXT,
  locked_widen INTEGER DEFAULT 0
);

CREATE TABLE regime_state (
  venue TEXT NOT NULL,
  underlying_group_id TEXT NOT NULL,
  regime TEXT NOT NULL,        -- bull_trend/bear_trend/chop/crisis
  confidence REAL NOT NULL,
  evidence_json TEXT NOT NULL,
  updated_ts INTEGER NOT NULL,
  PRIMARY KEY (venue, underlying_group_id)
);

ALTER TABLE positions ADD COLUMN entry_strategy_id TEXT;
ALTER TABLE positions ADD COLUMN active_strategy_id TEXT;
ALTER TABLE positions ADD COLUMN swap_count INTEGER DEFAULT 0;

CREATE TABLE position_strategy_segments (
  position_id TEXT NOT NULL,
  segment_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  regime_at_start TEXT NOT NULL,
  started_ts INTEGER NOT NULL,
  ended_ts INTEGER,
  entry_reason TEXT, exit_reason TEXT,
  attribution_weight REAL DEFAULT 0.0,
  pnl_r REAL DEFAULT 0.0
);

CREATE TABLE position_conviction_layers (
  position_id TEXT NOT NULL,
  layer_id TEXT PRIMARY KEY,
  parent_position_id TEXT,
  layer_index INTEGER NOT NULL,
  size_mult REAL NOT NULL,
  opened_ts INTEGER NOT NULL,
  strategy_id TEXT NOT NULL,
  venue TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL
);
```

### Function signatures
```python
# tick_recalc.py
def mark_position_dirty(position_id, reason, now_ts) -> None
def should_run_recalc(state, now_ts, cadence_sec=5) -> bool
def recompute_exit_params(position, market_snapshot, regime_state, learner_state) -> dict
async def run_live_recalc_cycle(now_ts, active_positions, max_positions=50) -> list[dict]

# regime_flip.py
def classify_regime(*, venue, underlying_group_id,
    bars_5m, bars_4h, atr_baseline_p50) -> dict
def detect_regime_flip(current_regime, candidate) -> dict | None
def publish_regime_flip_event(*, venue, symbol, underlying_group_id,
    from_regime, to_regime, now_ts) -> None
def apply_regime_flip_to_positions(*, venue, underlying_group_id,
    new_regime, now_ts) -> list[str]

# strategy_swap.py
def evaluate_strategy_swap(position, market_view, candidates) -> dict | None
def apply_strategy_swap(position_id, from_strategy, to_strategy,
    reason, now_ts) -> None
def compute_segment_attribution(segments, total_pnl_r) -> list[dict]

# conviction.py
def can_stack_conviction(position, portfolio_state, cell_quartile,
    unrealized_pnl_r) -> bool
def compute_stack_size_mult(existing_layers) -> float
def build_stack_signal(position, size_mult) -> dict

# adaptive_exit.py
def should_check_exit_override(position, state, now_ts,
    mid_price, atr_value) -> bool
def can_widen_exit(position, proposed_stop, hard_max_loss_r) -> bool
def apply_exit_override(position_id, proposed_stop, reason, now_ts) -> bool
```

### Constants (P0)
```python
LIVE_RECALC_CADENCE_SEC = 5
LIVE_RECALC_MAX_POSITIONS = 50
LIVE_RECALC_BATCH_P95_MS = 250
REGIME_FLIP_CONFIRM_CLOSES = 2
REGIME_CRISIS_ATR_RATIO = 2.2
REGIME_CRISIS_1H_MOVE_MULT = 2.5
SWAP_MAX_PER_TRADE = 1
SWAP_DEFAULT_ATTRIBUTION = (0.40, 0.60)
SWAP_LATE_ATTRIBUTION = (0.30, 0.70)
SWAP_LATE_HOLD_THRESHOLD = 0.70
CONVICTION_MAX_LAYERS = 3
CONVICTION_LAYER_MULTS = (1.0, 0.7, 0.5)
CONVICTION_MIN_PNL_R = 0.5
CONVICTION_GROUP_CAP_MULT = 2.2
EXIT_OVERRIDE_COOLDOWN_SEC = 30
EXIT_OVERRIDE_MAX_PER_TRADE = 5
EXIT_DIRTY_PRICE_ATR_MULT = 0.25
EXIT_DIRTY_PNL_BOUNDARIES_R = (0.7, 1.5, 2.5)
```

### P0 vs P1 split
- **P0**: tick-triggered dirty + 5s cadence + venue-scoped regime + 1-swap cap + 3-layer stacking + close-only override reset.
- **P1**: Sonnet position monitor 의 swap evaluation (현재 Python deterministic) + multi-position parallel monitor + 8-dim regime classifier 추가 axis.
- **P2**: cross-venue regime correlation graph (BTC OKX bull + Capital chop = arb 탐색).

## Risk + Aggressive Mitigation

### Risk
1. L6 가 "매 tick 모두 재계산 엔진" 으로 비대 → stale state + state thrash 동시 발생.
2. swap / stacking / override 가 독립 카운터 없이 섞여 attribution + cap audit 깨짐.

### Aggressive Mitigation
- `5s cadence + dirty trigger + venue-scoped regime + 1-swap cap + 3-layer stacking + close-only override reset` 6점 fix → 계산 가벼움 + regime flip / winner extension / cell top-quartile concentration 그대로 push.
- Conviction stacking = `wait` (rotate 금지) → winner 자르지 않음 = aggressive 보존.
- Override count reset = close only → quota 우회 차단 (regime flip/24h 으로 reset 거부).
- locked_widen 후에도 `protective exit` 는 계속 작동 → loss 무한대 X.

## Sources
- codex round 1 (`/tmp/polaris_phase0/L6_r1_response.md`, gpt-5.4)
- ADR-003 §Layer 6, ADR-006 §regime enum
- [[layer-2-per-gate-pipeline]] §Q8 swap + Q9 adaptive exit + 5초 cadence
- [[layer-3-sizing-risk]] §headroom min() (stacking 도 예외 X)
- [[layer-4-cell-matrix]] §top quartile (stacking 게이트)
- [[layer-1-canonical-baseline]] §market_events.regime_flip + baseline ratio
- T11 archive `live_exit_recalc` 모태
