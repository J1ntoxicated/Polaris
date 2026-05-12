---
type: component
component: layer-7-strategy-isolation
status: active
date_created: 2026-05-06
tags: [layer-7, isolation, circuit-breaker, allocator-fence, idempotent-orders]
related: [[ADR-003]], [[ADR-005]], [[ADR-008]], [[layer-3-sizing-risk]], [[layer-5-learner-network]]
reviewed_by: codex+jin (round 1, gpt-5.4)
---

# Layer 7 — Strategy Isolation Primitives

## Decision (codex 합의 R1)

**P0 = `asyncio task per strategy`** + **central allocator + central circuit breaker** (process boundary 거부, P1 이후 promote). **Circuit breaker = 4 states (ACTIVE / SOFT_HALT / HARD_HALT / RISK_ONLY)**, NaN 1회 즉시 hard, exception 3/300s hard, reject 3/600s soft, stale data timeframe-별. **Allocator hard fence = single global asyncio.Lock + reservation ledger 5s TTL** (SQLite locking 의존 X). **State namespace = single unified tables + explicit `strategy_id` 컬럼 + composite PK** (separate table/db 거부). **Idempotent key = `strategy_id:venue:symbol:timeframe:signal_ts:side`**, same-key+same-payload = retry-safe, same-key+different-payload = bug → strategy HALT 후보.

## Detail Spec

### Q1 — Process boundary: asyncio task (P0) + central services
P0 = **asyncio task per strategy** with supervisor + 중앙 allocator + 중앙 circuit breaker.

**P1 promote 조건** (선별 strategy → process):
- 동일 strategy 24h 내 `hard halt >= 2`
- strategy loop p95 latency > target × 2
- blocking vendor SDK 가 loop starvation 유발

**운영 규칙**:
- strategy = mutable portfolio state 직접 접근 **금지**.
- 매 cycle 시작 시 `AccountSnapshot` 1개 생성 → 7 task fan-out (immutable).
- task 예외는 supervisor 가 삼키지 않고 `circuit_breaker.record_fault()` 전달.
- CPU-heavy step 만 `run_in_executor`. strategy 전체 process 승격 = P1.

### Q2 — Circuit breaker (4 states + thresholds)
**States**:
- `ACTIVE`: 정상
- `SOFT_HALT`: 신규 entry 금지, 15분 auto-unblock
- `HARD_HALT`: 신규 entry 금지, manual reset only
- `RISK_ONLY`: HALT 중 active position 처리 모드 (close/stop update 만 허용)

**Thresholds**:
| Fault | Trigger | Result |
|---|---|---|
| exception | 3회 / 300s | HARD_HALT |
| order reject | 3회 / 600s | SOFT_HALT |
| NaN sizing | 1회 즉시 | HARD_HALT |
| stale data (1m strategy) | 75s | SOFT_HALT |
| stale data (15m strategy) | 300s | SOFT_HALT |
| stale data (1H strategy) | 900s | SOFT_HALT |

**HALT 시 처리**:
- 신규 entry / add / stack / swap 금지
- 기존 position close/stop update 허용 (RISK_ONLY)

**Learner triple-block 과 차이**:
- learner block: `(ticker, strategy, regime)` 범위, `size_mult=0.3`, **entry 허용** (edge 문제).
- circuit breaker: `strategy_id` 전체 범위, **신규 entry 자체 금지** (runtime integrity 문제).

### Q3 — Allocator hard fence (single global asyncio.Lock)
**Single-process + single global asyncio.Lock + reservation ledger** (SQLite locking 의존 X).

**Pattern**: `check → reserve → submit → confirm/release`.

**Cap order** (ADR-005 carryover): cluster cap → strategy cap → venue → global.

**TTL**: reservation expire = **5초**. submit timeout 시 즉시 재 reserve X, 먼저 기존 `order_key` 조회.

**Deadlock 회피**: fine-grained lock X, **one global lock**으로 끝냄 (7 strategies 규모 = lock 비용 무시 가능).

### Q4 — State namespace (single unified tables + strategy_id 컬럼)
**P0 정답**: `single table + explicit strategy_id column + composite PK/index`.

**거부**:
- separate tables per strategy (query complexity 증가).
- separate DB files (atomic portfolio snapshot + allocator consistency 악화).

**일관성**: L4 `cell_matrix` + L5 `learner_state` 도 explicit `strategy_id` 컬럼 포함.

### Q5 — Idempotent order keys
**Canonical key**: `strategy_id:venue:symbol:timeframe:signal_ts:side` (P0).

**충돌 처리**:
- same key + **same payload** → `dedupe_hit=True`, 기존 row 재사용 (retry-safe).
- same key + **different payload** → `IdempotencyConflictError`, `strategy_fault_events` 기록 → strategy HALT 후보.
- network timeout → 같은 `order_key` + `venue_client_id` 조회 후 없으면 재submit.

**Venue mapping**:
- OKX: `clOrdId = order_key`
- Capital: `dealReference = order_key`
- local order_key ↔ venue client id = 1:1 유지.

## Implementation notes

### File layout (P0)
```
polaris/core/isolation/
├── worker.py              # supervise_strategies + run_strategy_task
├── portfolio_snapshot.py  # AccountSnapshot immutable
├── namespace.py           # unified tables strategy_id helper
├── circuit_breaker.py     # 4-state + thresholds + record_fault
├── allocator_fence.py     # asyncio.Lock + reservation ledger
├── order_keys.py          # build_order_key + payload_hash
└── order_intents.py       # register_order_intent + resolve_duplicate
```

### Schema (SQLite)
```sql
CREATE TABLE strategy_halts (
  strategy_id TEXT NOT NULL,
  halt_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,          -- SOFT_HALT/HARD_HALT/RISK_ONLY
  reason_code TEXT NOT NULL,
  opened_ts INTEGER NOT NULL,
  unblock_ts INTEGER,
  reset_by TEXT, reset_ts INTEGER,
  detail_json TEXT NOT NULL
);

CREATE TABLE strategy_fault_events (
  event_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  fault_type TEXT NOT NULL,
  event_ts INTEGER NOT NULL,
  detail_json TEXT NOT NULL
);

CREATE TABLE allocator_reservations (
  reservation_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  venue TEXT NOT NULL, symbol TEXT NOT NULL,
  correlation_group TEXT NOT NULL,
  underlying_group_id TEXT NOT NULL,
  order_key TEXT NOT NULL,
  requested_notional REAL NOT NULL,
  requested_risk REAL NOT NULL,
  status TEXT NOT NULL,        -- pending/confirmed/released/expired
  created_ts INTEGER NOT NULL,
  expires_ts INTEGER NOT NULL,
  confirmed_ts INTEGER, released_ts INTEGER,
  venue_order_ref TEXT
);
CREATE UNIQUE INDEX idx_allocator_pending_order_key
  ON allocator_reservations(order_key)
  WHERE status IN ('pending', 'confirmed');

CREATE TABLE positions (
  position_id TEXT PRIMARY KEY,
  venue TEXT NOT NULL, symbol TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  entry_strategy_id TEXT NOT NULL,
  active_strategy_id TEXT NOT NULL,
  side TEXT NOT NULL, qty REAL NOT NULL,
  status TEXT NOT NULL
);
CREATE INDEX idx_positions_strategy_status ON positions(strategy_id, status);

CREATE TABLE orders (
  order_id TEXT PRIMARY KEY,
  venue TEXT NOT NULL, symbol TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  order_key TEXT NOT NULL,
  venue_client_id TEXT, venue_order_id TEXT,
  status TEXT NOT NULL,
  payload_hash TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_orders_strategy_order_key
  ON orders(strategy_id, order_key);

CREATE TABLE order_intents (
  order_key TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  venue TEXT NOT NULL, symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  signal_ts INTEGER NOT NULL, side TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  status TEXT NOT NULL,        -- created/submitted/acked/filled/rejected/cancelled
  venue_client_id TEXT, venue_order_id TEXT,
  created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL
);

CREATE TABLE risk_events (
  risk_event_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  created_ts INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);
```

### Function signatures
```python
@dataclass(frozen=True)
class AccountSnapshot:
    snapshot_id: str; created_ts: int
    positions: tuple; open_orders: tuple; balances: tuple
    venue_limits: dict[str, float]
    cluster_usage: dict[str, float]

# worker.py
async def run_strategy_task(strategy_id, strategy, bus, now_ts) -> None
async def supervise_strategies(strategies, bus) -> None
def build_account_snapshot(now_ts: int) -> AccountSnapshot

# circuit_breaker.py
def record_fault(*, strategy_id, fault_type, now_ts, detail) -> dict
def current_strategy_mode(strategy_id, now_ts) -> str
def should_allow_new_entry(strategy_id, now_ts) -> bool
def reset_strategy_halt(strategy_id, operator, now_ts) -> None

# allocator_fence.py
@dataclass
class AllocationRequest:
    strategy_id: str; venue: str; symbol: str; side: str
    correlation_group: str; underlying_group_id: str
    requested_notional: float; requested_risk: float
    signal_id: str; order_key: str
    ttl_sec: int = 5

async def check_and_reserve(req: AllocationRequest, now_ts) -> dict
async def confirm_reservation(reservation_id, venue_order_ref, now_ts) -> None
async def release_reservation(reservation_id, reason, now_ts) -> None
async def expire_stale_reservations(now_ts) -> int

# order_keys.py
def build_order_key(*, strategy_id, venue, symbol, timeframe,
    signal_ts, side) -> str
def payload_hash(order_payload: dict) -> str
def register_order_intent(*, order_key, strategy_id, payload_hash,
    now_ts) -> dict
def resolve_duplicate_intent(order_key, payload_hash) -> dict
```

### Constants (P0)
```python
ISOLATION_TASK_BOUNDARY = "asyncio"   # P0; "process" = P1 promote
CB_EXCEPTION_THRESHOLD = (3, 300)     # 3회 / 300초 → HARD
CB_REJECT_THRESHOLD = (3, 600)        # 3회 / 600초 → SOFT
CB_NAN_THRESHOLD = (1, 0)             # 1회 즉시 HARD
CB_STALE_DATA_BY_TIMEFRAME = {"1m": 75, "15m": 300, "1H": 900}
CB_SOFT_HALT_AUTO_UNBLOCK_SEC = 900
ALLOCATOR_RESERVATION_TTL_SEC = 5
P1_PROCESS_PROMOTE_HARD_HALT_24H = 2
```

### P0 vs P1 split
- **P0**: asyncio task + central allocator (one global Lock) + central circuit breaker + unified tables + idempotent key.
- **P1**: 선별 strategy → subprocess promote (조건 충족 시) + WebSocket fill stream (현재 REST polling) + venue 측 native idempotency 확장.
- **P2**: lock striping (cluster 별 sub-lock, 7+ strategy 확장 시).

## Risk + Aggressive Mitigation

### Risk
가장 큰 위험 = `asyncio` 자체 아님. **"task 분리만 해놓고 mutable shared state 열어둔 반쪽 isolation"** = race / duplicate submit / cap overrun / halt leakage 동시 발생.

### Aggressive Mitigation
- **Task = 가볍게**, 공유 쓰기 경로는 **3 군데만 허용** (`allocator_fence`, `order_intents`, `circuit_breaker`).
- Strategy code = `immutable AccountSnapshot → signal emit` 만.
- HALT 시에도 `RISK_ONLY` mode 로 active position 관리 → loss 방치 X.
- single global Lock 비용 = 7 strategies 규모에서 무시 가능, 구현 리스크 낮음 → 7 strategy day-1 동시 가동 가능.
- 고장 반경 = `strategy_id` 안에 갇힘 (다른 strategy 영향 X) → aggressive 7 strategy 동시 push.

## Sources
- codex round 1 (`/tmp/polaris_phase0/L7_r1_response.md`, gpt-5.4)
- ADR-003 §Layer 7 Round 3 D1 7 mechanism
- ADR-005 §cluster cap order (allocator 우선순위)
- ADR-008 §7 strategy day-1 동시 + per-strategy circuit breaker
- [[layer-3-sizing-risk]] §headroom min() (allocator 적용 위치)
- [[layer-5-learner-network]] §triple block (edge vs runtime 차이)

## Strategies isolated by this layer (each gets its own worker + circuit breaker)
[[volume_burst]] · [[tsmom]] · [[rsi_bb_pullback]] · [[spot_donchian]] · [[fx_breakout_basket]] · [[xau_indices_trend]] · [[session_breakout]]

## Implementation status (2026-05-07 — P0 Day 2)

- **Status**: implemented + codex round 3 APPROVE.
- **Code**: `polaris/core/isolation/{worker,namespace,circuit_breaker,allocator_fence,order_keys,__init__}.py` (~1 050 LOC)
  - `circuit_breaker.py` — pure `compute_4_state_transition` + DB-backed `record_fault` / `current_strategy_mode` / `should_allow_new_entry` / `reset_strategy_halt`. **Monotonic severity**: SOFT cannot downgrade open HARD/RISK_ONLY; SOFT auto-expires after 15 min; HARD/RISK_ONLY require manual reset. SOFT → HARD upgrade closes the soft row to keep severity unambiguous.
  - `allocator_fence.py` — `AllocatorFence` (single global asyncio.Lock) + `get_process_fence`/`reset_process_fence` (process-wide singleton guard) + `AllocationRequest` dataclass. Sweep-on-reserve auto-expires stale `pending` rows so a dead reservation cannot lock out a retry.
  - `worker.py` — `run_strategy_task` + `supervise_strategies`. ACTIVE → `tick(snapshot)`. **RISK_ONLY → `tick_risk_only(snapshot)` if implemented**, else skip (no entry hook). HARD/SOFT skip entirely. Exception path always feeds `record_fault`.
  - `namespace.py` — `StrategyNamespace` wrapper + module helpers; only ever read/write rows scoped to one `strategy_id`.
  - `order_keys.py` — canonical idempotent key + payload-hash dedupe + `IdempotencyConflictError` (records fault row for would-be HALT escalation).
- **Tests**: 28 unit + property tests (`tests/test_layer7_isolation.py`) covering monotonic severity (HARD cannot downgrade), RISK_ONLY exit hook, allocator fence race, idempotent order keys, namespace isolation between strategy A and B.
- **Schema rows**: `strategy_halts`, `strategy_fault_events`, `allocator_reservations` (+ unique partial index on order_key for live reservations), `order_intents`, `positions`, `orders`, `risk_events` (provisioned by `polaris/storage/schema.py::ALL_DDL`).

## Implementation status (2026-05-07 — Day 9 F11 wire)

- **F11 fix**: production paper loop now routes per-signal pipeline tasks through `supervise_pipeline_tasks` (Layer 7 SSOT) instead of bare `asyncio.create_task` + `asyncio.gather`. See `vault/40_ops/digests/2026-05-07_p1_day9_f11_f12_supervise_starting_capital.md`.
- New SSOT helper in `polaris/core/isolation/worker.py`:
  - `PipelineTaskSpec(strategy_id, coro_factory)` dataclass.
  - `async def supervise_pipeline_tasks(specs, *, conn, now_ts, fault_phase) -> list[dict]` — `asyncio.TaskGroup` (Python 3.13) + per-task fault catching → `record_fault(strategy_id=spec.strategy_id, ...)` so a strategy crash routes to the offending strategy's circuit breaker (not the legacy generic `"pipeline"` bucket).
  - `supervise_strategies` (per-strategy `tick`) also upgraded to TaskGroup.
- `_run_tick` calls `should_allow_new_entry(conn, strategy_id, now_ts)` BEFORE `generate_raw_signal` so HALTed strategies skip cleanly.
- `ProdLoopState.supervised_tasks_total / supervised_tasks_failed` counters surface in the operator log summary.
- **Tests**: `tests/test_supervise_strategies_wire.py` — 8 new tests covering empty-spec, TaskGroup pattern (sibling continues on raise), per-strategy fault recording, threshold→HARD_HALT, and source-level guards on the production loop call site.
