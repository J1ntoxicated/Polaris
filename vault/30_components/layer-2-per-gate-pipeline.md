---
type: component
component: layer-2-per-gate-pipeline
status: active
date_created: 2026-05-06
tags: [layer-2, pipeline, ai, langgraph, per-gate, haiku, sonnet]
related: [[ADR-003-8-layer-architecture|ADR-003]], [[ADR-004-per-gate-ai-pipeline|ADR-004]], [[ADR-006-cell-matrix|ADR-006]], [[ADR-007-learner-network|ADR-007]], [[layer-0-universe-discovery]], [[layer-1-canonical-baseline]]
reviewed_by: codex+jin (round 1, gpt-5.4)
---

# Layer 2 — Per-Gate AI Agent Pipeline

## Decision (codex 합의 R1)

**Custom Python asyncio orchestrator** (LangGraph X, CrewAI X) + **hybrid in-memory queue + SQLite gate_events** durability + **Haiku 3 gate (1/3/4) + Python 5 gate (2/5/6/7/8)** P0 + **mixed failure mode** (entry-side 3/4/5 fail-closed, position-side 6/7/8 protective fail-open) + **G4-only fast-path skip** + **same-correlation-group strategy swap** + **Adaptive Exit floor-only widening** + **AI lesson 100-trade soft mode**.

## Detail Spec

### Q1 — Orchestrator: custom asyncio + explicit state graph
P0 단일 dev / read·debug simplicity / dependency 0 / crash surface 축소. LangGraph 의 conditional edge / checkpoint 직접 구현 가능. CrewAI overkill.

**파일**:
- `polaris/core/pipeline/gate_orchestrator.py`
- `polaris/core/pipeline/gate_state.py`
- `polaris/core/pipeline/gate_registry.py`

**Edge 수**: 8 gate 선형 + 4 분기만 허용 (edge bug 봉쇄).

### Q2 — Communication: hybrid (memory hot path + SQLite append-only)
- Hot path = process memory (latency budget tight)
- Durability = `gate_events` append-only (crash recovery)
- Payload 전체 저장 X, `decision-critical fields only`

### Q3 — Model split P0 vs P1
| Gate | P0 | P1 |
|---|---|---|
| G1 Universe Scanner | **Haiku** | Haiku |
| G2 Strategy Signal | **Python** | Python |
| G3 Signal Validator | **Haiku** | Haiku |
| G4 Pre-Entry Watcher | **Haiku** | Haiku |
| G5 Entry Sizer | **Python** | Sonnet |
| G6 Position Monitor | **Python** | Sonnet |
| G7 Adaptive Exit | **Python** | Sonnet |
| G8 Post-Trade Reflector | **Python template** | Sonnet |

**Fallback chain**: 2단만 (`LLM gate primary → Python deterministic`). Sonnet→Haiku→Python 3단 거부 (latency/cost/state 폭발).

### Q4 — Failure mode (mixed policy)
**원칙**: new-risk-creation gates (3/4/5) = fail-closed. position-management gates (6/7/8) = protective fail-open.

| Gate | Failure | 이유 |
|---|---|---|
| G1 | fail-open (이전 cycle focus 재사용 max 2 cycle) | aggressive coverage 보존 |
| G2 | strategy-local fail-closed (다른 strategy 계속) | isolation primitive |
| G3 | fail-closed (kill 또는 deterministic veto) | risk gate |
| G4 | fail-closed (단 fast-path 승인 건만 skip) | 진입 전 보호 |
| G5 | fail-closed (size 실패 = 주문 금지) | risk gate |
| G6 | protective fail-open (default exit/trail 유지) | 열린 position 방치 X |
| G7 | fail-open (default ATR×N exit 복귀) | 보수 default |
| G8 | fail-open (lesson 누락 허용) | observability only |

### Q5 — Latency budget + parallel fan-out
**Sequential chain 유지**. 병렬화는 G2 strategy fan-out / symbol batch validation / multi-position monitor 만.

| Gate | Latency budget |
|---|---|
| G1 | < 1200ms |
| G2 | < 300ms per symbol batch |
| G3 | < 1200ms |
| G4 | 30s window, per-call < 800ms |
| G5 (Python) | < 50ms |
| G6/G7 (Python) | < 100ms per position |

15m bar 주력 = 5-15s OK. 1m 전략 = G4 skip 없이는 빡빡 → Q7 fast-path 활용.

### Q6 — Prompt design (per-gate custom + cached shared prefix)
**공통 prefix (Anthropic prompt cache, TTL 5min)**:
- system role / decision enum / strict JSON schema
- cell_matrix snapshot summary
- ticker baseline summary
- recent same-symbol trade digest

**Gate suffix**:
- G1: focus compression
- G3: PASS/KILL/MODIFY decision + few-shot 3개 (clean PASS / overextended KILL / borderline MODIFY)
- G4: PROCEED/KILL + few-shot 2개

**토큰 가이드**:
- G1: 2-3k in / <400 out
- G3: 1-2k in / <250 out
- G4: 0.8-1.5k in / <150 out (G4 는 live tick suffix 항상 uncached)

### Q7 — Fast-path: G4-only skip
**Skip 가능**: G4 (Pre-Entry Watcher) **만**. G1/G3 skip 금지 (validation 필수).

**G4 skip 조건 (모두 충족)**:
- `cell quartile == top`
- `validated_signal.strength >= 1.25`
- `spread_bps <= baseline_p50 * 0.9`
- `no same-symbol reject in last 6h`
- `listing_age_hours >= 24`
- `not session_open_shock_window`

= 검증 강하고 미세구조 평온한 ticker 만. Skip 시 IOC/limit entry only (chase 방지).

### Q8 — Mid-trade strategy swap (same correlation_group only)
**제약**: same correlation_group + same side + same venue/symbol. cross-group swap = 사실상 재진입 = 거부.

**Attribution split** — `position_strategy_segments` ledger:
```sql
CREATE TABLE position_strategy_segments (
  position_id TEXT, segment_id TEXT PRIMARY KEY,
  strategy_id TEXT,
  started_ts INTEGER, ended_ts INTEGER,
  entry_reason TEXT, exit_reason TEXT,
  pnl_r REAL DEFAULT 0.0
);
```

**cell_matrix update** (default 40/60):
- entry strategy 40% / exit-managed strategy 60%
- hold time 의 70% 이상이 후반 strategy = 30/70 로 조정

### Q9 — Adaptive Exit override (floor-only widening)
- Default ATR×N exit = **floor**.
- Override 채택 조건: long `new_stop_price < current_stop_price` (더 멀 때) / short 반대.
- Cadence: **5초 고정** (매 tick X).
- Cap: trade 당 **max 5회**, last override 후 **30s cooldown**.
- Hard rails:
  - `max_loss_r` hard cap 초과 X
  - `unrealized_pnl_r > +0.7R` 일 때만 widening 허용
  - BEP 아래로 다시 넓히는 것 금지

### Q10 — Post-Trade Reflector → AI feedback learner
- **Weight axis 3개**: `strategy_id × regime`, `strategy_id × session`, `validator_threshold_bias`.
- **Δ 범위**:
  - **P0**: `[-0.03, +0.03]` (보수)
  - **P1**: ADR-007 의 `[-0.1, +0.1]` 확장
- **Soft mode** (closed trades < 100): observe + shadow update only, 실제 sizing 영향 = **25%** 만 반영.
- **Live mode** (≥ 100 closed trades): 정상 weight Δ 반영.

**Lesson 적용 규칙**:
- `confidence < 0.70` → 버림
- deterministic facts 와 충돌 → 버림
- 같은 `lesson_type` 3연속 동일 방향일 때만 live bias 강화

```sql
CREATE TABLE ai_lessons (
  lesson_id TEXT PRIMARY KEY,
  trade_id TEXT, strategy_id TEXT,
  regime TEXT, session TEXT,
  confidence REAL,
  lesson_type TEXT,    -- entry_timing/exit_patience/overtrade/...
  delta_json TEXT,
  created_ts INTEGER
);
```

## Implementation status

| Gate | P0 (Python deterministic) | P1 (GPT) | Status |
|------|---------------------------|----------|--------|
| G1 Universe Scanner | ✅ top-N fallback | ✅ gpt-5-mini wired | Day 3 |
| G2 Strategy Signal | ✅ fan-out | n/a | Day 3 |
| G3 Signal Validator | ✅ Python rails | ✅ gpt-5-mini wired | Day 3 |
| G4 Pre-Entry Watcher | ✅ fast-path | ✅ gpt-5-mini wired | Day 3 |
| G5 Entry Sizer | ✅ T4 | n/a | Day 3 |
| **G6 Position Monitor** | ✅ Python rails | ✅ **gpt-5.5 wired** | **Day 9 F1** |
| **G7 Adaptive Exit** | ✅ Q9 widening rail | ✅ **gpt-5.5 wired** | **Day 9 F1** |
| G8 Post-Trade Reflector | ✅ Python template | ✅ gpt-5.5 wired (default in paper loop) | Day 9 F1 |

Live recalc loop ([[layer-6-live-recalc]] Q1 5s cadence): G6 + G7 fire per active position per tick (Day 9 F2 — `polaris/scripts/_production_recalc.py`). G6 EXIT_NOW triggers a **specific** close (not FIFO oldest) via `close_specific_position(position_id)`.

## Implementation notes

### File layout (P0)
```
polaris/core/pipeline/
├── gate_orchestrator.py      # asyncio chain
├── gate_state.py             # GateContext / GateResult dataclass
├── gate_registry.py          # gate_id → handler mapping
├── model_router.py           # haiku/python runtime select
├── failure_policy.py         # mixed fail-open/closed matrix
├── fast_path.py              # G4 skip eligibility
├── recovery.py               # resume from gate_events
├── post_trade_reflector.py   # G8 lesson generator
└── prompts/
    ├── shared_prefix.txt
    ├── gate_1_universe.txt
    ├── gate_3_validator.txt
    └── gate_4_pre_entry.txt
polaris/core/live_recalc/
├── strategy_swap.py
└── adaptive_exit.py
polaris/core/learners/
└── ai_feedback.py
polaris/storage/
└── event_store.py            # gate_events append-only
```

### Core dataclass + signatures
```python
@dataclass
class GateContext:
    run_id: str
    signal_id: str | None; position_id: str | None
    gate_id: int
    venue: str; symbol: str; strategy_id: str | None
    payload: dict; started_ts: int

@dataclass
class GateResult:
    decision: str   # PASS/KILL/MODIFY/PROCEED/HOLD/EXIT_NOW/SWAP
    next_gate: int | None
    payload: dict
    model_used: str # haiku/python/sonnet
    latency_ms: int
    error: str | None = None

async def run_gate(ctx: GateContext) -> GateResult
async def run_signal_pipeline(run_id: str, focus_batch: list[dict]) -> list[GateResult]
async def recover_inflight_runs(now_ts: int) -> list[str]

def choose_gate_runtime(gate_id: int, phase: str) -> str
def should_fast_path(validated_signal, cell_score, baseline) -> bool
def run_python_fallback(gate_id, payload) -> GateResult
def resolve_gate_failure(gate_id, error, ctx) -> GateResult

# parallel fan-out
async def generate_signals_parallel(focus: list[dict]) -> list[dict]
async def validate_signals_parallel(raw_signals, max_concurrency=16) -> list[dict]
async def monitor_positions_parallel(positions, max_concurrency=32) -> list[dict]

# strategy swap
def evaluate_strategy_swap(position, market_view, candidates) -> dict | None
def apply_strategy_swap(position_id, from_strategy, to_strategy, reason) -> None

# adaptive exit
def can_widen_exit(position, proposed_stop, hard_max_loss_r) -> bool
def should_check_exit_override(now_ts, position) -> bool

# AI feedback
def parse_reflector_lesson(closed_trade, llm_output) -> dict | None
def compute_ai_feedback_delta(lessons, sample_count) -> dict[str, float]
def apply_shadow_or_live_delta(delta, closed_trade_count) -> None
```

### gate_events schema
```sql
CREATE TABLE gate_events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT, signal_id TEXT, position_id TEXT,
  gate_id INTEGER,
  phase TEXT,        -- start/success/fail/timeout/fallback
  decision TEXT, model_used TEXT, latency_ms INTEGER,
  payload_json TEXT, error_text TEXT,
  created_ts INTEGER
);
CREATE INDEX idx_gate_events_run ON gate_events(run_id, gate_id, created_ts);
```

### P0 vs P1 split
- **P0**: custom asyncio + Haiku G1/G3/G4 + Python G2/G5-8 + mixed failure + G4 fast-path + same-group swap + 100-trade soft AI lesson.
- **P1**: G5/G6/G7/G8 Sonnet upgrade + AI lesson live mode + multi-position monitor full parallel.

## Risk + Aggressive Mitigation

### Risk
가장 큰 위험 = "AI gate 많음" 자체가 아니라 gate 별 fallback / skip / failure 기준이 제각각이라 시스템 정체성 흐려짐. 두 번째 = Haiku validation 품질 부족 + AI lesson hallucination.

### Aggressive Mitigation
- P0 = `Haiku 1/3/4 + Python rest + hybrid events + G4-only fast path + same-group swap only` 고정 → 공격성 유지하며 복잡도 폭발 봉쇄.
- Haiku schema 엄격화 + few-shot 강화 + Python veto rails (deterministic fact 충돌 시 LLM 무효).
- 진입 전만 fail-closed (3/4/5), position 관리는 protective fail-open (6/7/8) → 열린 position 절대 방치 X.
- Fast-path 허용 = top quartile 중 상위 절반 + IOC/limit entry only (chase 방지).
- Adaptive Exit widening = `unrealized_pnl_r > +0.7R` 후만 + BEP 아래로 다시 넓히기 금지.
- AI lesson 100-trade 소프트 모드 = hallucination 누적 방지하면서 aggressive 상시 활성.

## Sources
- codex round 1 (`/tmp/polaris_phase0/L2_r1_response.md`, gpt-5.4)
- ADR-004 §gate pipeline + cost estimate
- ADR-003 §Layer 2/6/7
- ADR-006 §cell quartile routing
- ADR-007 §AI feedback learner
- [[layer-0-universe-discovery]], [[layer-1-canonical-baseline]]
- R4 research: LangGraph / TradingAgents (analyst → risk → executor 패턴)
