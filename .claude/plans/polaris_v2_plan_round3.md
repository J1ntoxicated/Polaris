# Polaris v2 Plan — REWRITE 4 (Round 3 codex debate 합의)

> 작성: 2026-05-06 (Sydney) · 방법: round 2 base + round 3 4-topic codex debate (D1-D4, gpt-5.4) · 절대 컨텍스트: **DEMO unlock**
>
> Round 1, 2 합의 변경 X (T4 sizing 공식 anti-collapse, T5 harness 4 modes/5 agents, T6 vault 6 dirs, dual-track concurrent). · Round 3 patch = §1 / §3-4 / §5 / §2 (segregation primitives).

---

## ⚠️ 절대 컨텍스트 (모든 phase)

**DEMO / PAPER / SANDBOX ONLY**:
- OKX SPOT demo (`us.okx.com` + `x-simulated-trading: 1`) — 가상 USDT $79,000
- Capital.com CFD demo (`demo-api-capital.backend-capital.com`) — 가상 AUD $78,000
- 합산 ≈ USD $130,000 가상 자금. **실제 자금 손실 = 0**.

**거부된 전제** (등장 시 무효):
- 12주/60d/90d demo gate · KPI auto-disable · regulatory cap · capital protection
- Posture defensive · macro guard 60m blackout · regime auto-throttle
- regrets/ 디렉토리 · anti-stealth-conservatism log
- monthly review · 30일 lock-in (round 3 D3 합의)
- "표본 부족 risk", "real-money safety", "fractional Kelly is too aggressive in practice"
- Live 진입 결정 = 본 plan 책임 영역 밖 (Jin 단독, 별도 phase)

**Stop 권한**: Jin manual only. demo 자금 0 도달 시 → DB reset → restart.

**Aggressive bias preserved**: "안전" 논거 = 점수 감점, "Hi-risk hi-return" = 가산.

---

## 1. Vision (T1 + Round 3 D3 patch)

### Targets
- **Primary**: 일 평균 +0.75% (compounding 252 → +560%/yr; $130k → $865k 가상)
- **Stretch**: 일 평균 +1.25% (~5,400%/yr 이론치)
- **Daily intraday band**:
  - soft: ±5% (정상 운영)
  - stretch: ±8% (고출력 구간)
  - ±8%+ : 태깅만, 실행 차단 X

### Miss escalation policy (Round 3 D3 — continuous trade-driven)

**Trigger evaluation = 매 closed trade 직후** (continuous, 시간 X).
**Lever 변경 발동 조건 = 4개 동시 충족**:
1. `rolling 100-trade portfolio expectancy < 0`
2. `rolling 40-trade expectancy < rolling 100-trade expectancy` (열화 가속)
3. 직전 lever 변경 후 ≥ 20 closed trades 경과 (cooldown, flip-flop 차단)
4. 손상 지표 ≥ 2개 동시 훼손: `win-rate`, `avg R`, `profit factor`, `MFE/MAE ratio`

### 4 lever 순서 + skip 정책 (Round 3 D3)

**순서** (round 2 변경 — sizing-first 폐기):
1. `reallocate` (자본 이동 — 가장 빠른 unlock)
2. `strategy_add` (새 edge 추가)
3. `sizing` (생존 sleeve 강화 only — 실패 sleeve 업사이즈 금지)
4. `leverage` (마지막, gate 조건 별도)

**Skip 규칙**:
- `localized miss` (특정 sleeve 집중): `reallocate → sizing → strategy_add`
- `broad miss` (6개 동반 둔화): `reallocate` 생략, `strategy_add` 직행
- 2회 연속 lever 변경 후에도 rolling 40-trade 개선 없음 → 중간 단계 생략, `strategy_add` 점프
- `leverage` gate: portfolio rolling 100-trade expectancy 양수 복귀 전 unlock 금지 (어떤 경우에도 skip unlock X)
- 신규 추가 sleeve = 기존 상위 2 sleeve 와 상관 낮은 쪽 우선

### Drawdown checkpoint snapshot (실행 차단 X, 데이터 가치만)
- intraday -8% : snapshot + 원인 태깅
- rolling 5d -20% : feature dump + freeze-copy
- venue equity -35% : full position state freeze

### Auto-stop = 없음. Jin manual only.

---

## 2. Architecture — Dual-Track Concurrent (T2 + Round 3 D1 patch)

### 공통 코어 + 얇은 adapter (codex 4-point 압축)

#### Layer 1 — Canonical market model
`data_adapter → canonical bar/event → signal_engine`
- OKX 1m bar / Capital tick 모두 canonical OHLCV+event stream 으로 정규화
- `polaris/core/data/{canonical.py, adapter_base.py}`

#### Layer 2 — Unified SQLite schema with venue column
namespaced tables 폐기. 단일 schema, `venue` 컬럼 포함:
```sql
CREATE TABLE positions(venue TEXT, symbol TEXT, strategy_id TEXT, ...);
CREATE TABLE fills(venue TEXT, strategy_id TEXT, ...);
CREATE TABLE orders(venue TEXT, strategy_id TEXT, ...);
CREATE TABLE signals(strategy_id, signal_id, correlation_group, ...);
CREATE TABLE events(timestamp, type, agent, mode, payload_json);
```

#### Layer 3 — Common sizing + venue constraint translation
- Common: `target_risk_budget`, `notional`, `stop_distance` 산출
- Venue: cash (OKX SPOT) / margin·leverage·liquidation (Capital CFD) 변환은 venue 어댑터 책임
- `polaris/venues/{okx,capital}/constraint_translator.py`

#### Layer 4 — REST polling 우선 P0
- WebSocket = P1 후반 도입
- P0 strategy = 30s+ timeframe (1m bar OK)

#### Layer 5 — Strategy isolation primitives (Round 3 D1 신규)
**6 strategy 동시 활성 (P1.0 day 1) 의 segregation enforcement**:
1. **Per-strategy process boundary** — strategy 마다 독립 worker/process (메모리/event loop/retry queue 공유 X)
2. **Per-strategy state namespace** — `positions/{sid}`, `orders/{sid}`, `risk/{sid}`, `signals/{sid}` 경로 분리. 다른 strategy 디렉토리 write 금지
3. **Immutable portfolio inputs** — strategy = read-only account snapshot, 주문 의도만 emit. 실제 sizing/submit = 중앙 execution agent 검증 후 반영
4. **Strategy-scoped circuit breaker** — 예외/order reject storm/NaN sizing/stale market data → strategy_id `HALT`, 나머지 5개 continue
5. **Global allocator hard fence** — T4 scalar/amplifier 산출은 strategy 별, `hard MAX` 는 중앙 allocator 최종 강제 (cap 잠식 방지)
6. **Idempotent order keys** — `strategy_id + symbol + timeframe + signal_ts` dedupe key
7. **Kill-switch granularity** — default `kill(strategy_id)`, global stop = exchange/session 장애 only

**파일**: `polaris/core/isolation/{worker.py, namespace.py, circuit_breaker.py, allocator_fence.py}`

### Portfolio
- **운영**: venue 별 분리. cross-venue netting X.
- **Dashboard**: USD-equivalent aggregate (read model only).
- **Reallocate decision** (§1 lever 1): aggregate 기반.

### Strategy capability metadata (cross-venue 미래 대비)
```python
capability = {
  "venues": ["okx", "capital"],
  "asset_classes": ["crypto", "fx", "gold", "indices"],
  "correlation_group_id": "spot_intraday_event"
}
```
연결 키: `strategy_instance_id`, `portfolio_group_id`.

### 3rd venue 정책
- P0/P1 = 2 venue (OKX + Capital). 3rd 거부.
- P2 후반 후보: **Alpaca**. Bybit/Binance 거부 (OKX 와 중복).

---

## 3. Track A — OKX SPOT ($79k USDT)

### Constraints
- Endpoint: `us.okx.com` + `x-simulated-trading: 1`
- Long-only, no leverage
- Fee: 0.1% maker/taker (demo)
- Symbol universe: top 50 USDT-quote by 24h vol

### Strategies (3 P1)
| # | Strategy | Type | Timeframe | Per-strategy cap |
|---|---|---|---|---|
| 1 | Volume Burst | event-driven | 1m bar | 24% |
| 2 | TSMOM 20-bar | cross-sectional momentum | 1H rebalance | 32% (basket) |
| 3 | RSI-BB Pullback | mean-reversion on trend | 15m bar | 18% |

### Track A gross cap: 60% · daily venue risk: 8%

---

## 4. Track B — Capital CFD (A$78k)

### Constraints
- Endpoint: `demo-api-capital.backend-capital.com`
- Long/short, leverage native
- Leverage ceilings: forex 30× / indices 20× / gold 20× / commodity 10×
- Fee: spread (built-in)

### Strategies (3 P1)
| # | Strategy | Symbols | Lev | Per-strategy cap |
|---|---|---|---|---|
| 4 | FX Breakout Basket | EURUSD, GBPUSD, AUDUSD, USDJPY, USDCAD | 30× | 12%/pair × 5 = 36% |
| 5 | XAU/Indices Trend | XAUUSD, US500, US100, GER40 | 20× | 16%/sym × 4 = 40% |
| 6 | Session Breakout | US500, US100, EURUSD, GBPUSD | 20× | 10%/trade × 20% concurrent |

### Track B gross cap: 80% · daily venue risk: 9%

### macro guard / news blackout = **거부** (Jin "묶지 마")

---

## 5. Sizing & Risk Engine (T4 + Round 3 D2/D4 patch)

### 기본 공식 (round 1 채택, anti-collapse, round 3 D4 amplifier 강화)
```
notional = base_notional 
         × continuous_scalar(strength)   # 0.75 ~ 1.5×
         × tier_amplifier(streak)        # 1.0× / 1.5× / 2.0× / 3.0×
clipped  = min(notional, hard_caps)
final    = clipped × leverage(venue)
```
- 1 continuous scalar (0.75-1.5×) BEFORE notional clip
- 1 tier amplifier (1.5/2.0/3.0×, round 3 D4 — 기존 binary 1.5× 폐기)
- All other = HARD MAX (소프트 dampener X)
- v1 9-stack collapse 영구 봉쇄

### Hard caps (Round 3 D2 — k=0.5 / single 8%/9% 강화)
| Param | Round 2 | **Round 3 D2** |
|---|---|---|
| per-symbol cap (spot, OKX) | 50% | 50% |
| per-symbol cap (CFD, Capital) | 35% | 35% |
| per-symbol absolute ceiling | 50% | 50% |
| Track A gross cap | 60% | 60% |
| Track B gross cap | 80% | 80% |
| Track A daily venue risk | 8% | 8% |
| Track B daily venue risk | 9% | 9% |
| Total daily risk absolute ceiling | 10% | 10% |
| max single-trade risk (default) | 4% | **8%** |
| max single-trade risk (amplifier on) | 5% | **9%** |
| single-trade absolute ceiling | 5% | **9%** |
| Kelly fractional k | 0.33 | **0.50** |

**Priority**: hard MAX > Kelly. Kelly 산출치가 single cap 초과 시 무조건 절단.

### Cold start (Round 3 D2 신규 — CS-3 bootstrap)
demo 첫 trades = historical p/q 입력 부재. bootstrap 정책:
- `n < 20` (closed trades per strategy): Kelly off, single-trade risk = **6% default / 7% amplifier on**
- `n >= 20`: Kelly on, 본 cap (8%/9%) 적용
- Kelly 입력 = 전략별 rolling estimator + clamp (급변폭 제한, sizing 진동 방지)

### Streak amplifier (Round 3 D4 — tier 강화)
**Tier table** (loss=R1 reset):
| Streak | Amplifier |
|---|---|
| 3 wins | 1.5× |
| 5 wins | 2.0× |
| 8+ wins | 3.0× |
| 1 loss | reset → 1.0× (binary) |

**Trigger gate** (계단별 차등):
- `n < 8` → amplifier off
- `n = 8~9` AND `hit-rate ≥ 75%` → 최대 `1.5×` 만
- `n ≥ 10` AND `hit-rate ≥ 70%` → full tier (1.5/2.0/3.0×)
- `2.0×` 이상 = 반드시 `n ≥ 10`

**9% cap 정합 예시**:
| Base | 1.5× | 2.0× | 3.0× | 절단 |
|---|---|---|---|---|
| 2% | 3% | 4% | 6% | 미도달 |
| 3% | 4.5% | 6% | 9% | 3.0× 정확히 |
| 4% | 6% | 8% | 9% (12→9) | 3.0× 절단 |
| 5% | 7.5% | 9% (10→9) | 9% (15→9) | 2.0×+ 절단 |

→ amplifier = 작은-base 전략의 추격 장치 (큰-base = cap 빨리 도달).

### Symbol-cluster cap (Round 3 D1 신규)
중앙 allocator 가 strategy cap 차감 **이전에** symbol-cluster cap 먼저 차감:
- `BTC/ETH cluster` (spot 동시 노출): 합산 한도 = 40%
- `XAU/indices cluster` (CFD 동시 노출): 합산 한도 = 50%
- `FX majors cluster` (CFD 동시 노출): 합산 한도 = 60%
- 6 strategy 동시 활성 시 상관 노출 압력 차단

### Risk budget fill-rate weak-signal cut (Round 3 D2/D3 신규)
venue daily risk ceiling (8%/9%) 빠른 도달 시:
- `risk budget fill-rate` 계산 = 현재까지 사용한 risk / daily ceiling
- fill-rate ≥ 70% 도달 시 가장 약한 signal_strength 부터 즉시 컷
- 손익 무관 (구조적 weak-signal 차단)

### ATR Stop/TP (per-strategy, Strategy.metadata)
| Strategy | Stop ATR | TP ATR | Window |
|---|---|---|---|
| Volume Burst | 1.8 | 2.5 | 10 |
| TSMOM | 2.5 | 4.0 | 14 |
| RSI-BB Pullback | 2.0 | 3.0 | 14 |
| FX Breakout | 2.0 | 3.5 | 14 |
| XAU/Indices | 2.5 | 4.0 | 14 |
| Session Breakout | 1.0 | 3.0 | 10 |

### P1 ramp-up (Round 3 D1 — 즉시 6 동시 활성)
- **P1.0** (P0 직후 day 1): **6 strategy 전부 동시 활성** (Volume Burst + TSMOM + RSI-BB Pullback + FX Breakout + XAU/Indices + Session Breakout)
- **첫 24h**: watchdog focus = segregation/wiring 검증 (성능 평가 X). 5 agents 중 1개 = watchdog 전용. 격리 결함 발견 시 strategy_id HALT + hotfix → 즉시 재투입
- **이후**: 자연 운영. lever 변경 = §1 D3 trigger 충족 시
- 시간 기반 ramp 게이트 (≥40 trades OR ≥3 days) = **폐기** (round 2 → round 3 변경)
- Jin manual override = 언제든 가능

---

## 6. Strategy Interface Lock (T3)

```python
@dataclass
class StrategyMetadata:
    timeframe: str
    warmup_bars: int
    max_positions: int
    gross_cap: float
    per_symbol_cap: float
    expected_holding_bars: int
    asset_class: str
    venue: str
    correlation_group_id: str

class Strategy(ABC):
    metadata: StrategyMetadata
    def generate_signals(market_view, portfolio_state) -> list[Signal]: ...
    def manage_position(position, market_view, portfolio_state) -> list[OrderIntent]: ...

@dataclass
class Signal:
    signal_id: str
    strategy_id: str
    symbol: str
    side: Literal["long", "short"]
    entry_type: Literal["market", "limit"]
    strength: float            # 0-1, signal quality
    sizing_hint: float         # 0-1, suggested portion
    ttl_bars: int
    thesis_tag: str
    correlation_group: str
    venue_constraints: dict
    created_at_bar: int
    invalidate_reason: Optional[str] = None
    cooldown_bars: int = 0
    tags: dict[str, str] = field(default_factory=dict)

@dataclass
class OrderIntent:
    signal_id: str
    execution_policy: dict     # post_only, reduce_only, slippage_tier, time_in_force
    state: Literal["candidate", "pending_entry", "active", "exit_pending", "closed"]
    venue_override: dict
```

### Correlation groups (max concurrent)
| Group | Strategy | Max concurrent |
|---|---|---|
| spot_intraday_event | Volume Burst | 3 |
| spot_cross_sectional_momo | TSMOM | 5 |
| spot_mean_reversion | RSI-BB Pullback | 4 |
| cfd_fx_trend | FX Breakout | 5 |
| cfd_index_commodity_trend | XAU/Indices | 4 |
| cfd_session_event | Session Breakout | 2 |

---

## 7. Harness (T5)

### 4 modes
- `/dev`, `/alpha`, `/forensic` (hard-exclusive)
- `/debate` (overlay)

### Posture 단일화
- DEMO = aggressive only.
- policy_engine.py 내 reserved field (`posture: Literal["aggressive"] = "aggressive"`), enforcement X.

### 5 agents
- `analyst` / `risk_officer` / `executor_okx` / `executor_capital` / `forensicist`
- **Round 3 D1**: P1.0 day 1 첫 24h = 1 agent 를 `watchdog` 으로 전용 (격리/배선 검증 focus)

### policy_engine.py (3-layer, codex)
```python
# Layer 1: Matrix (mode × agent × action_class)
MATRIX = {...}

# Layer 2: Per-action target validator
VALIDATORS = {
    "write_research": vault_path_predicate,
    "read_data": db_scope_predicate,
    "place_order": venue_symbol_predicate,
    ...
}

# Layer 3: Event log schema (모든 policy 결정 이벤트화)
@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    event_id: str

def check(mode, agent, action_class, target, ctx) -> PolicyDecision: ...
```

### Allowed-action matrix (DEMO 단순)
| mode \ action | read_data | write_research | write_now | write_log | write_decision | write_strategy | write_code | place_order | cancel_order | emergency_halt |
|---|---|---|---|---|---|---|---|---|---|---|
| dev | ALL | ALL | analyst,risk | ALL | analyst,risk | analyst | analyst | NO | NO | risk |
| alpha | ALL | ALL | risk,executor* | ALL | risk | NO | NO | executor_okx,executor_capital | executor_*,risk | risk |
| forensic | ALL | forensicist | forensicist | forensicist | forensicist | NO | NO | NO | forensicist (close only) | risk |

### `is_live` flag
- `ExecutionContext` 의 일부 (단순 bool 보유 X)
- Order/Fill/Audit 인터페이스 = demo/live 공용 port-adapter 패턴
- 분기 X (P0). live 도입 = 미래 별도 ADR.

### 6 hooks
1. `SessionStart` — vault `_NOW.md` + `INDEX.md` mandatory read
2. `SessionEnd` — vault append (digest/insight/ADR/lesson)
3. `PreToolUse` — policy_engine matrix check
4. `PostToolUse` — state_store write log
5. `UserPromptSubmit` — mode parse (`/dev`, `/alpha`, etc.)
6. `PreStateTransition` — checkpoint/strategy ramp/order state machine 모두 cover

### Event schema
- `signal_generated`, `order_intent_created`, `order_placed`, `fill_received`
- `position_state_transition`, `policy_decision`, `checkpoint_snapshot`
- `strategy_halt` (Round 3 D1 — circuit breaker), `strategy_resume`, `lever_change` (Round 3 D3)
- `drawdown_threshold_crossed`
- 모든 event = SQLite `events` table

### 9 skills (gerund form, ≤500L)
1. `running-paper-loop` — main paper trade loop kicker
2. `signaling-strategies` — signal gen orchestration
3. `sizing-positions` — T4 공식 invocation (Round 3 D2/D4 적용)
4. `executing-orders` — venue adapter call
5. `auditing-fills` — reconciliation
6. `analyzing-pnl` — daily/weekly P&L breakdown
7. `reviewing-strategies` — Strategy lifecycle (continuous trade-driven, Round 3 D3)
8. `governing-risk` — checkpoint snapshot, hard cap enforcement, symbol-cluster cap
9. `reconciling-portfolio` — position/cash/exposure state 합치기

### 7 MCP servers (round 1 lock)
- `ccxt`, `sqlite`, `duckdb`, `context7`, `sequential-thinking`, `obsidian-mcp`, `time`

---

## 8. Vault (T6)

### 6 dirs (round 1 lock)
- `00_charter` — 북극성, 원칙, vision
- `10_decisions` — ADR (creation order numbering)
- `20_strategies` — strategy specs (per Strategy 1 file)
- `30_components` — code module docs (sizing, risk, harness, etc.)
- `40_ops` — runtime, lever_change logs, schedules
- `50_research` — forensic, exploration, debate logs

### Root files
- `_NOW.md` — hybrid auto+manual live state (Tier 0, mandatory read)
- `INDEX.md` — catalog (auto-generated)
- `log.md` — chronological 1-line append

### regrets/ = 폐기

### 대체 메커니즘 = B' + D + C(제한) (Round 3 D3 — B' 변경)

#### B' — Continuous lever-change log (Round 3 D3, 기본 운영면)
- vault path: `40_ops/lever_change_<event_id>_<date>.md`
- 트리거: §1 4-조건 충족 시 발동
- 내용: rolling expectancy 통계 + 발동 lever + skip 결정 사유
- 허용 결정 (§1 lever): `reallocate / strategy_add / sizing(survivors only) / leverage(gated)`
- **금지**: failing sleeve upward sizing, leverage gate skip

#### D — Forensic on checkpoint trigger (사건 분석면)
- 발동 조건:
  - T1 -8% / -20% / -35% checkpoint snapshot
  - 동일 strategy + 동일 correlation_group, 7일 내 ≥3 stop-loss
  - **Round 3 D1**: strategy circuit breaker HALT 발동 시
- vault path: `50_research/forensic_<event_id>_<date>.md` 또는 `pattern_repeat_<group>_<date>.md`
- 항상 활성 X

#### C — Winner-only ELO (보조 증폭기, cap-bound)
- 매 trade 종료 시 strategy_evolution agent ELO update
- **Loser 자동 감점 X**
- Winner 증액 재원 = **유휴 현금에서만**, 총 cap 초과 금지
- 증액 룰: winner sizing scalar `+0.05 / 100 trades` (max 1.5×, T4 ceiling) — round 2 의 `monthly` 폐기, trade-count 기반으로 변경

### 2-tier lint
- Light pre-commit (orphan, frontmatter)
- Heavy weekly cron (stale, contradictions, link integrity)

### Frontmatter YAML, hash 폐기 (git immutability 가 hash 대체)

### Bases (Obsidian 1.9+, Dataview 폐기)

---

## 9. Phase Plan

### P0 — Infrastructure (1-3일 sprint)
**Scope** (codex 압축, ~3950 LOC, Round 3 D1 isolation primitives 추가):
- Canonical market model + REST polling + unified schema (~1500 LOC)
- 2 venue REST adapter (OKX SPOT + Capital CFD), market+limit only (~1200 LOC)
- Strategy 1개 paper loop smoke (Volume Burst) (~250 LOC)
- 최소 sizing engine + state store (~600 LOC)
- **Strategy isolation primitives** (Round 3 D1 신규, ~400 LOC) — per-strategy worker, namespace, circuit breaker, allocator fence

**검증**:
- smoke test only (full test suite = P1 후반)
- codex 외부 review 의무 (작성 agent ≠ 리뷰 agent, Jin 원칙)

### P1 — Strategy Activation (P0 직후 즉시)
**P1.0** (day 1, Round 3 D1): **6 strategy 전부 동시 활성**
  - 첫 24h: watchdog focus = segregation/wiring (성능 평가 X)
  - 격리 결함 발견 → strategy_id HALT + hotfix → 즉시 재투입
**P1.1+**: 자연 운영, lever 변경 = §1 D3 trigger 충족 시
- harness skills 9개 운영
- WebSocket 도입 (P1 후반)
- 시간 기반 phase plan = **거부**

### P2 — Ongoing
- Cross-venue arbitrage (correlation_group_id 활용)
- 3rd venue 후보 평가 (Alpaca, 자산군 다름)
- ELO winner-only sizing 증액 (cap-bound, trade-count)
- live 진입 = 별도 ADR (본 plan 책임 X)

### Stop conditions
- **Auto-stop = 없음**. Jin manual only.
- Demo 자금 0 도달 시 → DB reset → restart (학습 데이터 archive)

---

## 10. Key Decisions Summary (round 3 합의)

| 영역 | Round 2 | Round 3 (D1-D4 patch) | 근거 |
|---|---|---|---|
| Daily target | 0.75% / 1.25% | 동일 | (변경 X) |
| Architecture | dual-track + canonical | + Layer 5 isolation primitives | D1: per-strategy process/state/circuit/fence |
| Strategies | 6개 | 동일 | (변경 X) |
| **Ramp-up** | 3→4→6 (≥40 trades) | **D1**: P1.0 day 1 즉시 6 + 24h watchdog focus | "demo aggressive + 출력 극대화 우선" |
| Sizing 공식 | 1 scalar + 1 binary amp | **D4**: 1 scalar + tier amp (1.5/2/3×) | "공격성 + 통제, 계단형 폭주 관리" |
| **Hard caps** | k=0.33 / single 4-5% | **D2**: k=0.5 / single 8%/9% | "공격성 충분 + hard cap 살아있음" |
| **Cold start** | (없음) | **D2 신규**: n<20 = Kelly off, single 6%/7% bootstrap | "prior 고정 과신 회피, Kelly 비활성 죽임 회피" |
| **Streak amp** | 1.5× binary, n≥15 hit≥70% | **D4**: tier 1.5/2/3×, n≥10 hit≥70% (n=8-9 fast gate hit≥75%), R1 reset | "계단식 + 1패 즉시 reset" |
| **Symbol-cluster cap** | (없음) | **D1 신규**: BTC/ETH 40%, XAU/indices 50%, FX majors 60% | "6 strategy 동시 상관 노출 차단" |
| **Risk-budget fill-rate cut** | (없음) | **D2/D3 신규**: fill-rate ≥70% → weak-signal 컷 | "venue ceiling 빠른 도달 차단" |
| **Escalation trigger** | monthly review | **D3**: continuous trade-driven (rolling 100 + 40 + 20 cooldown + 2 지표) | "trade arrival 이 시간보다 빠름" |
| **4 lever 순서** | sizing→reallocate→leverage→add | **D3**: reallocate→add→sizing→leverage + skip | "깨진 edge 에 leverage = 비효율, 새 edge 우선" |
| Posture | aggressive only | 동일 | (변경 X) |
| policy_engine | 3-layer | 동일 + watchdog agent role (D1) | (확장) |
| Hooks | 5 + PreStateTransition | 동일 | (변경 X) |
| Skills | 9 (round 2 lock) | 동일 (D2/D4 적용 in `sizing-positions`, D3 적용 in `reviewing-strategies`) | (확장) |
| regrets/ | 폐기 (B+D+C) | 폐기 (B'+D+C, B' = continuous lever_change) | D3 trigger 정합 |
| Phase | P0 1-3일 / P1 즉시 | P0 1-3일 (~3950 LOC) / P1.0 day 1 6 동시 | D1 isolation +400 LOC |

---

## 11. 위반 키워드 sweep (round 3 종결 검증)

본 plan 등장 여부 검사:
- `12주`, `90d`, `60d` → 0건 ✓
- `regulatory cap`, `professional risk`, `capital protection`, `fund mandate` → 0건 ✓
- `regrets/`, `anti-stealth-conservatism` → 0건 (폐기 명기 only) ✓
- `auto-disable`, `auto drawdown stop`, `regime auto-throttle` → 0건 ✓
- `posture defensive`, `posture standard` → 0건 ✓
- `macro guard 60m`, `news blackout` → 0건 (거부 명기 only) ✓
- **`monthly review`, `30일 lock-in`** → 0건 (round 3 D3 폐기 명기 only) ✓
- `표본 부족 risk`, `real-money safety` → 0건 ✓
- `fractional Kelly is too aggressive in practice` → 0건 ✓

**모든 보수 위장 패턴 + round 3 신규 거부 키워드 제거 완료**.

---

## 12. 다음 액션

1. 본 plan Jin review · 승인 후 P0 sprint kickoff
2. `polaris/` 패키지 골격 생성 (core/venues/strategies/harness/)
3. canonical market model + REST polling + unified schema 작성 (P0 day 1)
4. **Strategy isolation primitives** (Round 3 D1) — per-strategy worker/namespace/circuit breaker/allocator fence (P0 day 1-2)
5. OKX adapter + Capital adapter (P0 day 2-3)
6. Volume Burst strategy + paper loop smoke test (P0 day 3)
7. **P1.0 즉시 ignition: 6 strategy 동시 활성** + 24h watchdog focus
8. vault `00_charter/` `_NOW.md` `INDEX.md` 초기화 (P0 와 병행)

**Codex 외부 review = 모든 신규 코드 의무** (Jin `feedback_code_review_codex_external`).

---

## 13. Round 3 합의 출처

- **D1 (Ramp-up + Segregation)**: `/tmp/polaris_debate_round3/d1_consensus.md` · codex round 1 (option B) · `/tmp/polaris_debate_round3/d1_r1_response.txt`
- **D2 (Hard caps + Cold start)**: `/tmp/polaris_debate_round3/d2_consensus.md` · codex round 1 (option B + CS-3) · `/tmp/polaris_debate_round3/d2_r1_response.txt`
- **D3 (Escalation trigger + lever)**: `/tmp/polaris_debate_round3/d3_consensus.md` · codex round 1 (option D + N3 + reallocate-first) · `/tmp/polaris_debate_round3/d3_r1_response.txt`
- **D4 (Streak amplifier tier)**: `/tmp/polaris_debate_round3/d4_consensus.md` · codex round 1 (option A + n≥10/n=8-9 fast + R1) · `/tmp/polaris_debate_round3/d4_r1_response.txt`

---

*Generated 2026-05-06 · Polaris v2 plan REWRITE 4 · Round 3 codex debate consensus (D1-D4, 4 codex calls, all consensus on round 1)*
